from typing import Any, Iterable, Optional, Callable

from atopile.front_end import lofty, Instance, ObjectLayer, Equation
from atopile import address
from atopile.address import AddrStr


def _get_instance(addr: str) -> Instance:
    """Return the instance at the given address, doing our best to ensure it exists"""
    obj_cache = lofty._output_cache  # FIXME: this is a hack
    if addr not in obj_cache:
        root_addr = address.get_entry(addr)
        lofty.get_instance_tree(root_addr)
    return obj_cache[addr]


def get_children(addr: str) -> Iterable[AddrStr]:
    """Iterate over the children of a given address"""
    for child in _get_instance(addr).children.values():
        yield child.addr


# FIXME: this name is bad
def get_children_items(addr: str) -> Iterable[tuple[str, AddrStr]]:
    """Return tuples of all the children of a given address with their names"""
    for name, child in _get_instance(addr).children.items():
        yield name, child.addr


def get_data_dict(addr: str) -> dict[str, Any]:
    """
    Return the data at the given address
    """
    # FIXME: this is a hack around the fact that the getter won't currently return a subtree
    root_addr = address.get_entry(addr)
    lofty.get_instance_tree(root_addr)
    return lofty._output_cache[addr].data


def get_lock_data_dict(addr: str) -> dict[str, Any]:
    """
    Return the data at the given address
    """
    # TODO: write me irl
    return {}


def all_descendants(addr: str) -> Iterable[str]:
    """
    Return a list of addresses in depth-first order
    """
    for child in get_children(addr):
        yield from all_descendants(child)
    yield addr


def _make_dumb_matcher(pass_list: Iterable[str]) -> Callable[[str], bool]:
    """
    Return a filter that checks if the addr is in the pass_list
    """

    # TODO: write me irl
    def _filter(addr: AddrStr) -> bool:
        instance = lofty._output_cache[addr]
        for super_ in reversed(instance.supers):
            if super_.address in pass_list:
                return True
        return False

    return _filter


def _any_super_match(super: str) -> Callable[[str], bool]:
    """
    Return a filter that checks if the super is in the instance
    """

    # TODO: write me irl
    def _filter(addr: AddrStr) -> bool:
        instance = lofty._output_cache[addr]
        for super_ in reversed(instance.supers):
            if super_.super is not None:
                print(super_.super)
                if super in super_.super:
                    return True
        return False

    return _filter


match_components = _make_dumb_matcher(["<Built-in>:Component"])
match_modules = _make_dumb_matcher(["<Built-in>:Module"])
match_signals = _make_dumb_matcher(["<Built-in>:Signal"])
match_pins = _make_dumb_matcher("<Built-in>:Pin")
match_pins_and_signals = _make_dumb_matcher(["<Built-in>:Pin", "<Built-in>:Signal"])
match_interfaces = _make_dumb_matcher(["<Built-in>:Interface"])
match_sentinels = _make_dumb_matcher(
    [
        "<Built-in>:Component",
        "<Built-in>:Module" "<Built-in>:Signal",
        "<Built-in>:Pin",
        "<Built-in>:Interface",
    ]
)


def get_supers_list(addr: AddrStr) -> ObjectLayer:
    return lofty._output_cache[addr].supers


def get_next_super(addr: AddrStr) -> ObjectLayer:
    return get_supers_list(addr)[0]


def get_parent(addr: str) -> Optional[str]:
    """
    Return the parent of the given address
    """
    # TODO: write me irl
    if "::" not in addr:
        return None
    root_path, instance_path = addr.rsplit("::", 1)
    if "." in instance_path:
        return addr.rsplit(".", 1)[0]
    elif instance_path:
        return root_path


def iter_parents(addr: str) -> Iterable[str]:
    """Iterate over the parents of the given address"""
    while addr := get_parent(addr):
        yield addr


def get_links(addr: AddrStr) -> Iterable[tuple[AddrStr, AddrStr]]:
    """Return the links associated with an instance"""
    links = lofty._output_cache[addr].links
    for link in links:
        yield (link.source.addr, link.target.addr)


def get_equations(addr: AddrStr) -> Iterable[Equation]:
    """Return the equations associated with an instance"""
    for super_ in lofty._output_cache[addr].supers:
        for equation in super_.obj_def.equations:
            yield equation
