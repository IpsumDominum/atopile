from pathlib import Path
from typing import List, Dict
import datetime

from attrs import define, field
from jinja2 import Environment, FileSystemLoader

from atopile.model.model import Model, VertexType, EdgeType
from atopile.model.utils import generate_uid_from_path

@define
class KicadField:
    name: str  # eg?
    value: str  # eg?

@define
class KicadPin:
    """
    eg. (pin (num "1") (name "") (type "passive"))
    """
    num: str
    name: str = ""
    type: str = ""

@define
class KicadLibpart:
    """
    eg.
    (libpart (lib "Device") (part "R")
      (description "Resistor")
      (docs "~")
      (footprints
        (fp "R_*"))
      (fields
        (field (name "Reference") "R")
        (field (name "Value") "R")
        (field (name "Datasheet") "~"))
      (pins
        (pin (num "1") (name "") (type "passive"))
        (pin (num "2") (name "") (type "passive"))))
    """
    lib: str
    part: str
    description: str
    docs: str
    footprints: List[str] = field(factory=list)
    fields: List[KicadField] = field(factory=list)
    pins: List[KicadPin] = field(factory=list)

@define
class KicadSheetpath:
    """
    eg. (sheetpath (names "/") (tstamps "/"))
    """
    names: str = "/" # module path, eg. toy.ato/Vdiv1
    tstamps: str = "/" # module UID, eg. b1d41e3b-ef4b-4472-9aa4-7860376ef0ce

@define
class KicadComponent:
    """
    eg.
    (comp (ref "R4")
      (value "R")
      (libsource (lib "Device") (part "R") (description "Resistor"))
      (property (name "Sheetname") (value ""))
      (property (name "Sheetfile") (value "example.kicad_sch"))
      (sheetpath (names "/") (tstamps "/"))
      (tstamps "9c26a741-12df-4e56-baa6-794e6b3aa7cd")))
    """
    ref: str  # eg. "R1" -- should be unique, we should assign these here I think
    value: str  # eg. "10k" -- seems to be an arbitary string
    libsource: KicadLibpart
    tstamp: str  # component UID, eg. b1d41e3b-ef4b-4472-9aa4-7860376ef0ce
    footprint: str = "" # eg. "Resistor_SMD:R_0603_1608Metric"
    properties: List[KicadField] = field(factory=list)
    fields: List[KicadField] = field(factory=list)
    sheetpath: KicadSheetpath = field(factory=KicadSheetpath)
    # TODO: tstamp should be consistent across runs and ideally track with updates

class KicadNode:
    """
    eg. (node (ref "R1") (pin "1") (pintype "passive"))
    """
    def __init__(self, component: KicadComponent, pin: KicadPin) -> None:
        self._component = component
        self._pin = pin

    @property
    def ref(self) -> str:
        return self._component.ref

    @property
    def pin(self) -> str:
        return self._pin.num

    @property
    def pintype(self) -> str:
        return self._pin.type

@define
class KicadNet:
    """
    eg.
    (net (code "1") (name "Net-(R1-Pad1)")
      (node (ref "R1") (pin "1") (pintype "passive"))
      (node (ref "R2") (pin "1") (pintype "passive")))
    """
    code: str  # do these have to be numbers, or can they be more UIDs?
    name: str  # TODO: how do we wanna name nets?
    nodes: List[KicadNode] = field(factory=list)

class KicadLibraries:
    """
    eg.
    (libraries
    (library (logical "Device")
      (uri "/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols//Device.kicad_sym")))
    """
    def __init__(self):
        # don't believe these are mandatory and I don't think they're useful in the context of atopile
        raise NotImplementedError

# TODO: fuck this thing right off
def designator_generator():
    """
    Spit out designators.
    TODO: make them things other than "Ax"
    """
    i = 1
    while True:
        yield f"A{i}"
        i += 1

@define
class KicadNetlist:
    version: str = "E"  # What does this mean?
    source: str = "unknown"  # eg. "/Users/mattwildoer/Projects/SizzleSack/schematic/sandbox.py" TODO: point this at the sourcefile
    date: str = ""
    tool: str = "atopile"  # TODO: add version in here too

    components: List[KicadComponent] = field(factory=list)
    libparts: List[KicadLibpart] = field(factory=list)
    nets: List[KicadNet] = field(factory=list)
    """
    (net (code "3") (name "Net-(R2-Pad2)")
      (node (ref "R2") (pin "2") (pintype "passive"))
      (node (ref "R3") (pin "1") (pintype "passive"))
      (node (ref "R4") (pin "1") (pintype "passive")))
    """

    def to_file(self, path: Path) -> None:
        # Create a Jinja2 environment
        # this_dir = Path(__file__).parent
        this_dir = Path("/Users/mattwildoer/Projects/atopile/src/atopile/netlist/netlist_generator.py").parent
        env = Environment(loader=FileSystemLoader(this_dir))

        # Load the component template and render
        comp_template = env.get_template("component_template.j2")
        components_string = comp_template.render(components=self.components)

        # Load the libpart template and render
        libpart_template = env.get_template("libpart_template.j2")
        libparts_string = libpart_template.render(libparts=self.libparts)

        # Load the net template and render
        net_template = env.get_template("net_template.j2")
        nets_string = net_template.render(nets=self.nets)

        # Create the complete netlist
        template = env.get_template("netlist_template.j2")
        netlist_str = template.render(nl=self, components_string=components_string, libparts_string=libparts_string, nets_string=nets_string)

        with path.open("w") as f:
            f.write(netlist_str)

    #TODO: I don't like this function living in the dataclass
    @classmethod
    def from_model(cls, model: Model, main: str) -> "KicadNetlist":
        """
        :param model: to generate the netlist from
        :param main: path in the graph to compile from
        """
        netlist = cls()

        # Extract the components under "main"
        designator = designator_generator()
        NON_FIELD_DATA = ["value", "footprint"]

        # Extract the components under "main"
        # TODO: move at least large chunks of this elsewhere. It's too entangled with the guts of the Model class
        part_of_view = model.get_graph_view([EdgeType.part_of])
        instance_of_view = model.get_graph_view([EdgeType.instance_of])
        main_vertex = model.graph.vs.find(path_eq=main)
        vidxs_within_main = part_of_view.subcomponent(main_vertex.index, mode="in")

        component_vs = model.graph.vs[vidxs_within_main].select(type_eq=VertexType.component.name)
        component_class_vidxs: Dict[str, int] = {}  # by component path
        for component_v in component_vs:
            component_class_vidx = instance_of_view.neighbors(component_v.index, mode="out")
            if len(component_class_vidx) < 1:
                component_class_vidxs[component_v["path"]] = component_v.index
            else:
                component_class_vidxs[component_v["path"]] = component_class_vidx[0]

        unique_component_class_vidxs = set(component_class_vidxs.values())

        # Create all the pins under main
        pins_by_path: Dict[str, KicadPin] = {}  # by component class's pin path
        pins_by_ref_by_component: Dict[str, Dict[str, KicadPin]] = {}  # by component class's pin path
        for component_class_idx in unique_component_class_vidxs:
            component_class_v = model.graph.vs[component_class_idx]
            component_class_path = component_class_v["path"]
            vidxs_within_component_class = part_of_view.subcomponent(component_class_idx, mode="in")
            pin_vs = model.graph.vs[vidxs_within_component_class].select(type_eq=VertexType.pin.name)

            for pin_v in pin_vs:
                pin_ref = pin_v["ref"].lstrip("p")
                pin = KicadPin(
                    num=pin_ref,
                    name=pin_ref,
                    type="",   # TODO:
                )

                pins_by_path[pin_v["path"]] = pin
                pins_by_ref_by_component.setdefault(component_class_path, {})[pin_v["ref"]] = pin

        # Create the libparts (~component_classes)
        libparts: Dict[str, KicadLibpart] = {}  # by component class path

        for component_class_idx in unique_component_class_vidxs:
            component_class_v = model.graph.vs[component_class_idx]
            component_class_path = component_class_v["path"]
            vidxs_within_component_class = part_of_view.subcomponent(component_class_v.index, mode="in")

            # Create the pins
            pin_vs_within_component_class = model.graph.vs[vidxs_within_component_class].select(type_eq=VertexType.pin.name)
            pin_paths_within_component_class = pin_vs_within_component_class["path"]
            component_class_pins = [pins_by_path[p] for p in pin_paths_within_component_class]

            fields = [KicadField(k, v) for k, v in model.data.get(component_class_path, {}).items() if k not in NON_FIELD_DATA]

            # Create the libpart
            libpart = KicadLibpart(
                lib=component_class_path,  # FIXME: this may require sanitisation (eg. no slashes, for Kicad)
                part=component_class_v["ref"],
                description=component_class_v["ref"],  # recycle ref here. TODO: should we consdier python-like doc-strings?
                fields=fields,
                pins=component_class_pins,
                # TODO: something better for these:
                docs="~",
                footprints=["*"],
            )

            libparts[component_class_path] = libpart

        # Create the component instances
        components: Dict[str, KicadComponent] = {}  # by component path
        nodes: Dict[str, KicadNode] = {}  # by component pin path
        for component_v in component_vs:
            component_path = component_v["path"]
            component_class_idx = component_class_vidxs[component_path]
            component_class_v = model.graph.vs[component_class_idx]
            component_class_path = component_class_v["path"]

            component_data = model.data.get(component_path, {})

            fields = [KicadField(k, v) for k, v in component_data.items() if k not in NON_FIELD_DATA]

            # there should always be at least one parent, even if only the file
            component_parent_idx = part_of_view.neighbors(component_v.index, mode="out")[0]
            component_parent_v = model.graph.vs[component_parent_idx]
            # TODO: deal with the sheets, I'm pretty sure they also need to be defined in a header somewhere
            # either way, I think there's more to it. Just chuck everything in root for now
            sheetpath = KicadSheetpath()

            component = KicadComponent(
                ref=next(designator),
                value=component_data.get("value", ""),
                libsource=libparts[component_class_path],
                tstamp=generate_uid_from_path(component_path),
                fields=fields,
                sheetpath=sheetpath
            )

            components[component_path] = component

            # Generate the component's nodes
            pins_by_ref = pins_by_ref_by_component[component_class_path]
            for ref, pin in pins_by_ref.items():
                nodes[f"{component_path}/{ref}"] = KicadNode(component=component, pin=pin)

        # Create the nets
        electrical_graph = model.get_graph_view([EdgeType.connects_to])
        electrical_graph_within_main = electrical_graph.subgraph(vidxs_within_main)
        clusters = electrical_graph_within_main.connected_components(mode="weak")
        nets = []
        for i, cluster in enumerate(clusters):
            cluster_vs = electrical_graph_within_main.vs[cluster]
            cluster_paths = cluster_vs["path"]

            # this works naturally to filter out signals, because only pins are present in the nodes dict
            nodes_in_cluster = [nodes[path] for path in cluster_paths if path in nodes]

            # let's find something to call this net
            # how about we call it {lowest common module path}.{signal-name-1}-{signal-name-2}-{signal-name-3}...
            # actually, fuck that, it sounds hard and it almost 11pm...
            # let's just call it i for now. TODO: ^ that better thing

            # the cluster only represents a net if it contains eletrical pins
            if nodes_in_cluster:
                net = KicadNet(
                    code=str(len(nets)),
                    name=str(i),
                    nodes=nodes_in_cluster,
                )

                nets.append(net)

        netlist = KicadNetlist(
            source=main,
            date=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            components=list(components.values()),
            libparts=list(libparts.values()),
            nets=nets,
        )

        return netlist