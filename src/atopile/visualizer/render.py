from contextlib import contextmanager
from typing import Dict, List, Optional

import attrs
import logging

from atopile.model.model import EdgeType, Model, VertexType
from atopile.model.utils import generate_uid_from_path
from atopile.model.accessors import ModelVertexView
from atopile.model.visitor import ModelVisitor

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)


@attrs.define
class Position:
    x: int
    y: int

    def to_dict(self) -> dict:
        return {
            "x": self.x,
            "y": self.y,
        }

@attrs.define
class Pin:
    # mandatory external
    name: str
    id: str
    index: int

    # mandatory internal
    location: str
    source_vid: int
    source_path: str
    block_uuid_stack: List[str]

    # optional external
    private: bool = False

    # optional internal
    connection_stubbed: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "uuid": self.id,
            "index": self.index,
            "private": self.private,
        }

@attrs.define
class Stub:
    name: str
    source: Pin
    id: str
    direction: str
    position: Optional[Position] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "source": self.source,
            "uuid": self.id,
            "direction": self.direction,
            "position": self.position.to_dict() if self.position is not None else None,
        }

@attrs.define
class Port:
    name: str
    id: str
    location: str
    pins: List[Pin]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "uuid": self.id,
            "location": self.location,
            "pins": [pin.to_dict() for pin in self.pins],
        }

@attrs.define
class Link:
    name: str
    id: str
    source: Pin
    target: Pin

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "uuid": self.id,
            "source": self.source.id,
            "target": self.target.id,
        }

@attrs.define
class Block:
    name: str
    type: str
    id: str
    blocks: List["Block"]
    ports: List[Port]
    links: List[Link]
    stubs: List[Stub]
    instance_of: Optional[str] = None
    position: Optional[Position] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "uuid": self.id,
            "blocks": [block.to_dict() for block in self.blocks],
            "ports": [port.to_dict() for port in self.ports],
            "links": [link.to_dict() for link in self.links],
            "stubs": [stub.to_dict() for stub in self.stubs],
            "instance_of": self.instance_of,
            "position": self.position.to_dict() if self.position is not None else None,
        }

# FIXME: this should go to something intelligent instead of this jointjs hack
# eg. up, down, left, right
pin_location_stub_direction_map = {
    "top": "top",
    "bottom": "bottom",
    "left": "left",
    "right": "right",
}

default_stub_direction = list(pin_location_stub_direction_map.values())[0]

def generate_stub_id(connection_id: str, endpoint: str) -> str:
    return generate_uid_from_path(connection_id + endpoint)

class Bob(ModelVisitor):
    """
    The builder... obviously
    """

    def __init__(self, model: Model, vis_data: dict) -> None:
        self.model = model
        self.vis_data = vis_data
        self.block_uuid_stack: List[str] = []
        self.block_directory_by_uuid: Dict[str, Block] = {}
        self.block_directory_by_path: Dict[str, Block] = {}
        self.pin_directory: Dict[int, Pin] = {}
        super().__init__(model)

    @contextmanager
    def block_context(self, block: str):
        self.block_uuid_stack.append(block)
        yield
        self.block_uuid_stack.pop()

    def get_position(self, path: str) -> Optional[Position]:
        # TODO: move this to the class responsible for handling visualisation configs
        # check if there's position data for this entity
        block_vis_data = self.vis_data.get(path, {})
        try:
            return Position(
                x=block_vis_data["position"]["x"],
                y=block_vis_data["position"]["y"]
            )
        except KeyError:
            log.debug("No position data for block %s", path)

    def get_the_position(self, ref: str, file: str) -> Optional[Position]:
        # TODO: move this to the class responsible for handling visualisation configs
        # check if there's position data for this entity
        test_data = {
            'LT3477.ato' : {
                'c_soft_start' : {
                    'position': {
                        'x' : 10,
                        'y': 10
                    }
                }
            }
        }
        file_vis_data = test_data.get(file, {})
        ref_vis_data = file_vis_data.get(ref, {})
        try:
            print('found pos')
            return Position(
                x=ref_vis_data["position"]["x"],
                y=ref_vis_data["position"]["y"]
            )
        except KeyError:
            print('yo')
            log.debug("No position data for ref %s in module %s", ref, file)

    def find_lowest_common_ancestor(self, pins: List[Pin]) -> str:
        if len(pins) == 0:
            raise RuntimeError("No pins to check for lowest common ancestor")
        if len(pins) == 1:
            return pins[0].id
        for i in range(min(len(p.block_uuid_stack) for p in pins)):
            # descend into the block stack

            uuids: List[str] = [p.block_uuid_stack[i] for p in pins]
            if not all(uuids[0] == puuid for puuid in uuids):
                # if all the blocks aren't the same, then our last check was... well our last
                if i < 0:
                    raise RuntimeError("No common ancestor found -- how are these two things even linked..?")
                lowest_common_ancestor = pins[0].block_uuid_stack[i-1]
                break
        else:
            lowest_common_ancestor = pins[0].block_uuid_stack[i]
        return lowest_common_ancestor

    def build(self, main: ModelVertexView) -> Block:
        root = self.generic_visit_block(main)

        stubbed_pins_vids: List[int] = []

        connections = self.model.graph.es.select(type_eq=EdgeType.connects_to.name)
        for connection in connections:
            source_pin = self.pin_directory.get(connection.source)
            target_pin = self.pin_directory.get(connection.target)
            block = self.block_directory_by_path.get(self.model.data.get(connection["uid"], {}).get("defining_block"), root)
            if source_pin is None or target_pin is None:
                # assume the pin isn't within the scope of the main block
                continue

            if source_pin.connection_stubbed or target_pin.connection_stubbed:
                if source_pin.connection_stubbed and target_pin.connection_stubbed:
                    raise NotImplementedError(f"Both pins {source_pin.id} and {target_pin.id} are stubbed")
                if source_pin.connection_stubbed:
                    stubbed_pin = source_pin
                    connecting_pin = target_pin
                else:
                    stubbed_pin = target_pin
                    connecting_pin = source_pin

                stub_name = stubbed_pin.source_path[len(main.path)+1:]
                if stubbed_pin.source_vid not in stubbed_pins_vids:
                    stubbed_id = generate_stub_id(connection["uid"], stubbed_pin.source_path)
                    block.stubs.append(Stub(
                        name=stub_name,
                        source=stubbed_pin.id,
                        id=stubbed_id,
                        direction=pin_location_stub_direction_map.get(stubbed_pin.location, default_stub_direction),
                        position=self.get_position(stubbed_id),
                    ))
                    stubbed_pins_vids.append(stubbed_pin.source_vid)

                connecting_id = generate_stub_id(connection["uid"], connecting_pin.source_path)
                block.stubs.append(Stub(
                    name=stub_name,
                    source=connecting_pin.id,
                    id=connecting_id,
                    direction=pin_location_stub_direction_map.get(connecting_pin.location, default_stub_direction),
                    position=self.get_position(connecting_id),
                ))

            else:
                link = Link(
                    name="test",  # TODO: give these better names
                    id=connection["uid"],
                    source=source_pin,
                    target=target_pin,
                )
                block.links.append(link)

        return root

    def generic_visit_block(self, vertex: ModelVertexView) -> Block:
        uuid_to_be = vertex.path
        with self.block_context(uuid_to_be):
            # find subelements
            blocks: List[Block] = self.wander(
                vertex=vertex,
                mode="in",
                edge_type=EdgeType.part_of,
                vertex_type=[VertexType.component, VertexType.module]
            )

            pins: List[Pin] = self.wander(
                vertex=vertex,
                mode="in",
                edge_type=EdgeType.part_of,
                vertex_type=[VertexType.pin, VertexType.signal]
            )
            # filter out Nones
            pins = [p for p in pins if p is not None]

            # pin locations specify ports they'll belong to
            pin_locations = {}
            for pin in pins:
                pin_locations.setdefault(pin.location, []).append(pin)

            ports: List[Port] = []
            for location, pins_at_location in pin_locations.items():
                ports.append(Port(
                    name=location,
                    id=f"{uuid_to_be}/port@{location}",
                    location=location,
                    pins=pins_at_location
                ))

            for i, pin in enumerate(pins):
                pin.index = i

            # building a vertex view to find the parent file of each block
            # TODO: might be compute intensive to do that
            block_instance = ModelVertexView.from_path(self.model, uuid_to_be)
            parent_file = block_instance.get_module_file()
            print('module is', uuid_to_be, 'ref is', block_instance.ref, 'parent is', parent_file.path)
            position = self.get_the_position(block_instance.ref, parent_file.path)

            # check if there's position data for this block
            position = self.get_position(uuid_to_be)

            # check the type of this block
            instance_ofs = vertex.get_adjacents("out", EdgeType.instance_of)
            if len(instance_ofs) > 0:
                instance_of = instance_ofs[0].ref
            else:
                instance_of = None

            # do block build
            block = Block(
                name=vertex.ref,
                type=vertex.vertex_type.name,
                id=uuid_to_be,
                blocks=blocks,
                ports=ports,
                links=[],
                stubs=[],
                instance_of=instance_of,
                position=position,
            )

            self.block_directory_by_uuid[uuid_to_be] = block
            self.block_directory_by_path[vertex.path] = block

        return block

    def visit_component(self, vertex: ModelVertexView) -> Block:
        return self.generic_visit_block(vertex)

    def visit_module(self, vertex: ModelVertexView) -> Block:
        return self.generic_visit_block(vertex)

    def generic_visit_pin(self, vertex: ModelVertexView) -> Pin:
        vertex_data = self.model.data[vertex.path]
        pin = Pin(
            name=vertex.ref,
            id=vertex.path,
            index=None,
            location=vertex_data.get("visualizer", {}).get("location", "top"),
            source_vid=vertex.index,
            source_path=vertex.path,
            block_uuid_stack=self.block_uuid_stack.copy(),
            connection_stubbed=vertex_data.get("visualizer", {}).get("stub", False),
            private=vertex_data.get("private", False),
        )

        self.pin_directory[vertex.index] = pin

        return pin

    def visit_pin(self, vertex: ModelVertexView) -> Optional[Pin]:
        # filter out pins that have a single connection to a signal within the same block
        connections_in = vertex.get_edges(mode="in", edge_type=EdgeType.connects_to)
        connections_out = vertex.get_edges(mode="out", edge_type=EdgeType.connects_to)
        if len(connections_in) + len(connections_out) == 1:
            if len(connections_in) == 1:
                target = ModelVertexView(self.model, connections_in[0].source)
            if len(connections_out) == 1:
                target = ModelVertexView(self.model, connections_out[0].target)
            if target.vertex_type == VertexType.signal:
                if target.parent_path == vertex.parent_path:
                    return None

        return self.generic_visit_pin(vertex)

    def visit_signal(self, vertex: ModelVertexView) -> Pin:
        return self.generic_visit_pin(vertex)

# TODO: resolve the API between this and build_model
def build_view(model: Model, root_node: str, vis_data: dict) -> list:
    root_node = ModelVertexView.from_path(model, root_node)
    bob = Bob(model, vis_data)
    root = bob.build(root_node)
    return [root.to_dict()]
