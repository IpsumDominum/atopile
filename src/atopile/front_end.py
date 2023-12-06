"""
This datamodel represents the code in a clean, simple and traversable way,
but doesn't resolve names of things.
In building this datamodel, we check for name collisions, but we don't resolve them yet.
"""
import enum
import logging
from collections import ChainMap
from contextlib import contextmanager
from functools import partial
from itertools import chain
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Callable

import toolz
from antlr4 import ParserRuleContext
from attrs import define, field, resolve_types

from atopile import errors
from atopile.address import AddrStr, add_instance
from atopile.datatypes import KeyOptItem, KeyOptMap, Ref
from atopile.generic_methods import recurse
from atopile.parse_utils import get_src_info_from_ctx
from atopile.parser.AtopileParser import AtopileParser as ap
from atopile.parser.AtopileParserVisitor import AtopileParserVisitor

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)


@define
class Base:
    """Represent a base class for all things."""
    src_ctx: Optional[ParserRuleContext] = field(kw_only=True, default=None)

@define
class Import(Base):
    """Represent an import statement."""
    obj_addr: AddrStr

    def __repr__(self) -> str:
        return f"<Import {self.obj_addr}>"

@define
class Replacement(Base):
    """Represent a replacement statement."""
    new_super_ref: Ref


@define(repr=False)
class ObjectDef(Base):
    """
    Represent the definition or skeleton of an object
    so we know where we can go to find the object later
    without actually building the whole file.

    This is mainly because we don't want to hit errors that
    aren't relevant to the current build - instead leaving them
    to be hit in the case we're actually building that object.
    """

    super_ref: Optional[Ref]
    imports: Mapping[Ref, Import]

    local_defs: Mapping[Ref, "ObjectDef"]
    replacements: Mapping[Ref, Replacement]

    # attached immediately to the object post construction
    closure: Optional[tuple["ObjectDef"]] = None  # in order of lookup
    address: Optional[AddrStr] = None

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} {self.address}>"


@define(repr=False)
class ObjectLayer(Base):
    """
    Represent a layer in the object hierarchy.
    This holds all the values assigned to the object.
    """

    # information about where this object is found in multiple forms
    # this is redundant with one another (eg. you can compute one from the other)
    # but it's useful to have all of them for different purposes
    obj_def: ObjectDef

    # None indicates that this is a root object
    super: Optional["ObjectLayer"]

    # the local objects and vars are things we navigate to a lot
    # objs: Optional[Mapping[str, "Object"]] = None
    data: Optional[Mapping[str, Any]] = None

    # data from the lock-file entry associated with this object
    # lock_data: Mapping[str, Any] = {}  # TODO: this should point to a lockfile entry

    @property
    def address(self) -> AddrStr:
        return self.obj_def.address

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} {self.obj_def.address}>"


resolve_types(ObjectLayer)


## The below datastructures are created from the above datamodel as a second stage


@define
class LinkDef(Base):
    """
    Represent a connection between two connectable things.

    # TODO: we may not need this using loop-soup
    # the reason this currently exists is to allow us to map joints between instances
    # these make sense only in the context of the pins and signals, which aren't
    # language fundamentals as much as net objects - eg. they're useful only from
    # a specific electrical perspective
    # origin_link: Link
    """

    source: Ref
    target: Ref

    def __repr__(self) -> str:
        return f"<LinkDef {repr(self.source)} -> {repr(self.target)}>"


@define
class Link(Base):
    """Represent a connection between two connectable things."""

    # TODO: we may not need this using loop-soup
    # the reason this currently exists is to allow us to map joints between instances
    # these make sense only in the context of the pins and signals, which aren't
    # language fundamentals as much as net objects - eg. they're useful only from
    # a specific electrical perspective
    # origin_link: Link

    parent: "Instance"
    source: "Instance"
    target: "Instance"

    def __repr__(self) -> str:
        return f"<Link {repr(self.source)} -> {repr(self.target)}>"


@define
class Instance(Base):
    """
    Represents the specific instance, capturing, the story you told of
    how to get there in it's mapping stacks.
    """

    # origin information
    # much of this information is redundant, however it's all highly referenced
    # so it's useful to have it all at hand
    addr: AddrStr
    supers: tuple["ObjectLayer"]
    children: dict[str, "Instance"]
    links: list[Link]

    data: Mapping[str, Any]  # this is a chainmap inheriting from the supers as well

    override_data: dict[str, Any]
    _override_location: dict[
        str, ObjectLayer
    ] = {}  # FIXME: this is a hack to define it here

    # TODO: for later
    # lock_data: Optional[Mapping[str, Any]] = None

    # attached immediately after construction
    parents: Optional[tuple["Instance"]] = None

    def __repr__(self) -> str:
        return f"<Instance {self.ref}>"


resolve_types(LinkDef)
resolve_types(Instance)
resolve_types(Link)


class _Sentinel(enum.Enum):
    NOTHING = enum.auto()


NOTHING = _Sentinel.NOTHING


def make_obj_layer(
    address: AddrStr, super: Optional[ObjectLayer] = None
) -> ObjectLayer:
    """Create a new object layer from an address and a set of supers."""
    obj_def = ObjectDef(
        address=address,
        super_ref=Ref.empty(),
        imports={},
        local_defs={},
        replacements={},
    )
    return ObjectLayer(
        obj_def=obj_def,
        super=super,
        data={},
    )


MODULE: ObjectLayer = make_obj_layer(AddrStr("<Built-in>:Module"))
COMPONENT: ObjectLayer = make_obj_layer(AddrStr("<Built-in>:Component"), super=MODULE)
PIN: ObjectLayer = make_obj_layer(AddrStr("<Built-in>:Pin"))
SIGNAL: ObjectLayer = make_obj_layer(AddrStr("<Built-in>:Signal"))
INTERFACE: ObjectLayer = make_obj_layer(AddrStr("<Built-in>:Interface"))


BUILTINS_BY_REF = {
    Ref.from_one("MODULE"): MODULE,
    Ref.from_one("COMPONENT"): COMPONENT,
    Ref.from_one("INTERFACE"): INTERFACE,
    Ref.from_one("PIN"): PIN,
    Ref.from_one("SIGNAL"): SIGNAL,
}


BUILTINS_BY_ADDR = {
    MODULE.address: MODULE,
    COMPONENT.address: COMPONENT,
    PIN.address: PIN,
    SIGNAL.address: SIGNAL,
    INTERFACE.address: INTERFACE,
}


class BaseTranslator(AtopileParserVisitor):
    """
    Dizzy is responsible for mixing cement, sand, aggregate, and water to create concrete.
    Ref.: https://www.youtube.com/watch?v=drBge9JyloA
    """

    def __init__(
        self,
        error_handler: errors.ErrorHandler,
    ) -> None:
        self.error_handler = error_handler
        super().__init__()

    def defaultResult(self):
        """
        Override the default "None" return type
        (for things that return nothing) with the Sentinel NOTHING
        """
        return NOTHING

    def visit_iterable_helper(
        self, children: Iterable
    ) -> KeyOptMap:
        """
        Visit multiple children and return a tuple of their results,
        discard any results that are NOTHING and flattening the children's results.
        It is assumed the children are returning their own OptionallyNamedItems.
        """

        _errors = []

        def __visit(child: ParserRuleContext) -> Iterable[_Sentinel | KeyOptItem]:
            try:
                child_result = self.visit(child)
                for item in child_result:
                    if item is not NOTHING:
                        assert isinstance(item, KeyOptItem)
                return child_result
            except errors.AtoError as err:
                _errors.append(err)
                self.error_handler.handle(err)
                return KeyOptMap.empty()

        child_results = list(__visit(child) for child in children)
        if _errors:
            raise ExceptionGroup("Errors occured in nested statements", _errors)

        child_results = chain.from_iterable(child_results)
        child_results = list(item for item in child_results if item is not NOTHING)
        child_results = KeyOptMap(KeyOptItem(cr) for cr in child_results)

        return KeyOptMap(child_results)

    def visit_ref_helper(
        self,
        ctx: ap.NameContext
        | ap.AttrContext
        | ap.Name_or_attrContext
        | ap.Totally_an_integerContext,
    ) -> Ref:
        """
        Visit any referencey thing and ensure it's returned as a reference
        """
        if isinstance(
            ctx,
            (
                ap.NameContext,
                ap.Totally_an_integerContext,
            ),
        ):
            return Ref.from_one(str(self.visit(ctx)))
        if isinstance(ctx, ap.Numerical_pin_refContext):
            name_part = self.visit_ref_helper(ctx.name_or_attr())
            return name_part.add_name(str(self.visit(ctx)))
        if isinstance(ctx, (ap.AttrContext, ap.Name_or_attrContext)):
            return Ref(
                map(str, self.visit(ctx)),
            )
        raise errors.AtoError(f"Unknown reference type: {type(ctx)}")

    def visitName(self, ctx: ap.NameContext) -> str:
        """
        If this is an int, convert it to one (for pins), else return the name as a string.
        """
        return ctx.getText()

    def visitAttr(self, ctx: ap.AttrContext) -> Ref:
        return Ref(self.visitName(name) for name in ctx.name())

    def visitName_or_attr(self, ctx: ap.Name_or_attrContext) -> Ref:
        if ctx.name():
            name = self.visitName(ctx.name())
            return Ref.from_one(name)
        elif ctx.attr():
            return self.visitAttr(ctx.attr())

        raise errors.AtoError("Expected a name or attribute")

    def visitString(self, ctx: ap.StringContext) -> str:
        return ctx.getText().strip("\"'")

    def visitBoolean_(self, ctx: ap.Boolean_Context) -> bool:
        return ctx.getText().lower() == "true"

    def visitSimple_stmt(
        self, ctx: ap.Simple_stmtContext
    ) -> Iterable[_Sentinel | KeyOptItem]:
        """
        This is practically here as a development shim to assert the result is as intended
        """
        result = self.visitChildren(ctx)
        for item in result:
            if item is not NOTHING:
                assert isinstance(item, KeyOptItem)
        return result

    def visitStmt(self, ctx: ap.StmtContext) -> KeyOptMap:
        """
        Ensure consistency of return type.
        We choose to raise any below exceptions here, because stmts can be nested,
        and raising exceptions serves as our collection mechanism.
        """
        if ctx.simple_stmts():
            stmt_returns = self.visitSimple_stmts(ctx.simple_stmts())
            return stmt_returns
        elif ctx.compound_stmt():
            item = self.visit(ctx.compound_stmt())
            if item is NOTHING:
                return KeyOptMap.empty()
            assert isinstance(item, KeyOptItem)
            return KeyOptMap.from_item(item)

        raise TypeError("Unexpected statement type")

    def visitSimple_stmts(
        self, ctx: ap.Simple_stmtsContext
    ) -> KeyOptMap:
        return self.visit_iterable_helper(ctx.simple_stmt())

    def visitBlock(self, ctx) -> KeyOptMap:
        if ctx.stmt():
            return self.visit_iterable_helper(ctx.stmt())
        if ctx.simple_stmts():
            return self.visitSimple_stmts(ctx.simple_stmts())
        raise ValueError  # this should be protected because it shouldn't be parseable

    def visitAssignable(self, ctx: ap.AssignableContext) -> int | float | str | bool:
        """Yield something we can place in a set of locals."""
        if ctx.name_or_attr():
            raise errors.AtoError(
                "Cannot directly reference another object like this. Use 'new' instead."
            )

        if ctx.NUMBER():
            value = float(ctx.NUMBER().getText())
            return int(value) if value.is_integer() else value

        if ctx.string():
            return self.visitString(ctx)

        if ctx.boolean_():
            return self.visitBoolean_(ctx.boolean_())

        assert (
            not ctx.new_stmt()
        ), "New statements should have already been filtered out."
        raise TypeError(f"Unexpected assignable type {type(ctx)}")

class Scoop(BaseTranslator):
    """Scoop's job is to map out all the object definitions in the code."""

    def __init__(
        self,
        error_handler: errors.ErrorHandler,
        ast_getter: Callable[[str | Path], ParserRuleContext],
        search_paths: Iterable[Path | str],
    ) -> None:
        self.ast_getter = ast_getter
        self.search_paths = search_paths
        self._output_cache: dict[AddrStr, ObjectDef] = {}
        super().__init__(error_handler)

    def get_obj_def(self, addr: AddrStr) -> ObjectDef:
        """Returns the ObjectDef for a given address."""
        if addr not in self._output_cache:
            assert addr.file is not None
            file_ast = self.ast_getter(addr.file)
            obj = self.visitFile_input(file_ast)
            assert isinstance(obj, ObjectDef)
            # this operation puts it and it's children in the cache
            self._register_obj_tree(obj, AddrStr(addr.file), ())
        return self._output_cache[addr]

    def _register_obj_tree(
        self, obj: ObjectDef, addr: AddrStr, closure: tuple[ObjectDef]
    ) -> None:
        """Register address info to the object, and add it to the cache."""
        obj.address = addr
        obj.closure = closure
        child_closure = (obj,) + closure
        self._output_cache[addr] = obj
        for ref, child in obj.local_defs.items():
            assert len(ref) == 1
            assert isinstance(ref[0], str)
            child_addr = addr.add_node(ref[0])
            self._register_obj_tree(child, child_addr, child_closure)

    def visitFile_input(self, ctx: ap.File_inputContext) -> ObjectDef:
        """Visit a file input and return it's object."""
        locals_ = self.visit_iterable_helper(ctx.stmt())

        # FIXME: clean this up, and do much better name collision detection on it
        local_defs = {}
        imports = {}
        for ref, local in locals_:
            if isinstance(local, ObjectDef):
                local_defs[ref] = local
            elif isinstance(local, Import):
                assert ref is not None
                imports[ref] = local
            else:
                raise errors.AtoError(f"Unexpected local type: {type(local)}")

        file_obj = ObjectDef(
            src_ctx=ctx,
            super_ref=Ref.from_one("MODULE"),
            imports=imports,
            local_defs=local_defs,
            replacements={},
        )

        return file_obj

    def visitBlockdef(self, ctx: ap.BlockdefContext) -> KeyOptItem[ObjectDef]:
        """Visit a blockdef and return it's object."""
        if ctx.FROM():
            if not ctx.name_or_attr():
                raise errors.AtoSyntaxError("Expected a name or attribute after 'from'")
            block_super_ref = self.visit_ref_helper(ctx.name_or_attr())
        else:
            block_super_ref = self.visitBlocktype(ctx.blocktype())

        locals_ = self.visitBlock(ctx.block())

        # FIXME: this needs far better name collision detection
        local_defs = {}
        imports = {}
        replacements = {}
        for ref, local in locals_:
            if isinstance(local, ObjectDef):
                local_defs[ref] = local
            elif isinstance(local, Import):
                imports[ref] = local
            elif isinstance(local, Replacement):
                replacements[ref] = local
            else:
                raise errors.AtoError(f"Unexpected local type: {type(local)}")

        block_obj = ObjectDef(
            src_ctx=ctx,
            super_ref=block_super_ref,
            imports=imports,
            local_defs=local_defs,
            replacements=replacements,
        )

        block_name = self.visit_ref_helper(ctx.name())

        return KeyOptItem.from_kv(block_name, block_obj)

    def visitImport_stmt(self, ctx: ap.Import_stmtContext) -> KeyOptMap:
        from_file: str = self.visitString(ctx.string())
        import_what_ref = self.visit_ref_helper(ctx.name_or_attr())

        _errors = []

        if not from_file:
            _errors.append(
                errors.AtoError("Expected a 'from <file-path>' after 'import'")
            )
        if not import_what_ref:
            _errors.append(
                errors.AtoError("Expected a name or attribute to import after 'import'")
            )

        if import_what_ref == "*":
            # import everything
            raise NotImplementedError("import *")

        # get the current working directory
        current_file, _, _ = get_src_info_from_ctx(ctx)
        current_file = Path(current_file)
        if current_file.is_file():
            search_paths = chain((current_file.parent,), self.search_paths)
        else:
            search_paths = self.search_paths

        for search_path in search_paths:
            candidate_path: Path = (search_path / from_file).resolve().absolute()
            if candidate_path.exists():
                break
        else:
            raise errors.AtoImportNotFoundError.from_ctx(  # pylint: disable=raise-missing-from
                f"File '{from_file}' not found.", ctx
            )

        if _errors:
            raise errors.AtoErrorGroup.from_ctx(
                "Errors occured in nested statements", _errors, ctx
            )

        import_addr = AddrStr.from_parts(path=candidate_path, ref=import_what_ref)

        import_ = Import(
            src_ctx=ctx,
            obj_addr=import_addr,
        )

        return KeyOptMap.from_kv(import_what_ref, import_)

    def visitBlocktype(self, ctx: ap.BlocktypeContext) -> Ref:
        """Return the address of a block type."""
        block_type_name = ctx.getText()
        match block_type_name:
            case "module":
                return Ref.from_one("MODULE")
            case "component":
                return Ref.from_one("COMPONENT")
            case "interface":
                return Ref.from_one("INTERFACE")
            case _:
                raise errors.AtoError(f"Unknown block type '{block_type_name}'")

    def visitRetype_stmt(self, ctx: ap.Retype_stmtContext) -> KeyOptMap:
        """TODO:"""
        # TODO: we should check the validity of the replacement here

        to_replace = self.visit_ref_helper(ctx.name_or_attr(0))
        new_class = self.visit_ref_helper(ctx.name_or_attr(1))

        replacement = Replacement(
            src_ctx=ctx,
            new_super_ref=new_class,
        )

        return KeyOptMap.from_kv(to_replace, replacement)

    def visitSimple_stmt(
        self, ctx: ap.Simple_stmtContext
    ) -> Iterable[_Sentinel | KeyOptItem]:
        """We have to be selective here to deal with the ignored children properly."""
        if ctx.retype_stmt() or ctx.import_stmt():
            return super().visitSimple_stmt(ctx)

        return KeyOptMap.empty()


def lookup_obj_in_closure(context: ObjectDef, ref: Ref) -> AddrStr:
    """
    This method finds an object in the closure of another object, traversing import statements.
    """
    assert context.closure is not None
    for scope in context.closure:
        obj_lead = scope.local_defs.get(ref[:1])
        import_leads = {
            imp_ref: imp
            for imp_ref, imp in scope.imports.items()
            if ref[0] == imp_ref[0]
        }

        if import_leads and obj_lead:
            # TODO: improve error message with details about what items are conflicting
            raise errors.AtoAmbiguousReferenceError.from_ctx(
                f"Name '{ref[0]}' is ambiguous in '{scope}'.", scope.src_ctx
            )

        if obj_lead is not None:
            return AddrStr.from_parts(
                obj_lead.address.file, obj_lead.address.ref + ref[1:]
            )

        if ref in scope.imports:
            return scope.imports[ref].obj_addr

    if ref in BUILTINS_BY_REF:
        return BUILTINS_BY_REF[ref].address

    raise KeyError(ref)


class Dizzy(BaseTranslator):
    """Dizzy's job is to create object layers."""

    def __init__(
        self,
        error_handler: errors.ErrorHandler,
        obj_def_getter: Callable[[AddrStr], ObjectDef],
    ) -> None:
        self.obj_def_getter = obj_def_getter
        self._output_cache: dict[AddrStr, ObjectLayer] = {
            k: v for k, v in BUILTINS_BY_ADDR.items()
        }
        super().__init__(error_handler)

    def get_obj_layer(self, addr: AddrStr) -> ObjectLayer:
        """Returns the ObjectLayer for a given address."""
        if addr not in self._output_cache:
            obj_def = self.obj_def_getter(addr)
            obj = self.make_object(obj_def)
            assert isinstance(obj, ObjectLayer)
            self._output_cache[addr] = obj
        return self._output_cache[addr]

    def make_object(self, obj_def: ObjectDef) -> ObjectLayer:
        """Create an object layer from an object definition."""
        ctx = obj_def.src_ctx
        assert isinstance(ctx, (ap.File_inputContext, ap.BlockdefContext))
        if obj_def.super_ref is not None:
            super_addr = lookup_obj_in_closure(obj_def, obj_def.super_ref)
            super = self.get_obj_layer(super_addr)
        else:
            super = None

        # FIXME: visiting the block here relies upon the fact that both
        # file inputs and blocks have stmt children to be handled the same way.
        if isinstance(ctx, ap.BlockdefContext):
            ctx_with_stmts = ctx.block()
        else:
            ctx_with_stmts = ctx
        locals_ = self.visitBlock(ctx_with_stmts)

        # TODO: check for name collisions
        data = {ref[0]: v for ref, v in locals_}

        obj = ObjectLayer(
            src_ctx=ctx_with_stmts,  # here we save something that's "block-like"
            obj_def=obj_def,
            super=super,
            data=data,
        )

        return obj

    def visitFile_input(self, ctx: ap.File_inputContext) -> None:
        """I'm not sure how we'd end up here, but if we do, don't go down here"""
        raise RuntimeError("File inputs should not be visited")

    def visitBlockdef(self, ctx: ap.BlockdefContext) -> _Sentinel:
        """Don't go down blockdefs, they're just for defining objects."""
        return NOTHING

    def visitAssign_stmt(self, ctx: ap.Assign_stmtContext) -> KeyOptMap:
        assignable_ctx = ctx.assignable()
        assert isinstance(assignable_ctx, ap.AssignableContext)
        if assignable_ctx.new_stmt():
            # ignore new statements here, we'll deal with them in future layers
            return KeyOptMap.empty()

        assigned_value_ref = self.visitName_or_attr(ctx.name_or_attr())
        if len(assigned_value_ref) > 1:
            # we'll deal with overrides later too!
            return KeyOptMap.empty()

        assigned_value = self.visitAssignable(ctx.assignable())
        return KeyOptMap.from_kv(assigned_value_ref, assigned_value)

    def visitSimple_stmt(
        self, ctx: ap.Simple_stmtContext
    ) -> Iterable[_Sentinel | KeyOptItem]:
        """We have to be selective here to deal with the ignored children properly."""
        if ctx.assign_stmt():
            return super().visitSimple_stmt(ctx)

        return (NOTHING,)


class Lofty(BaseTranslator):
    """Lofty's job is to walk orthogonally down (or really up) the instance tree."""

    def __init__(
        self,
        error_handler: errors.ErrorHandler,
        obj_layer_getter: Callable[[AddrStr], ObjectLayer],
    ) -> None:
        self._output_cache: dict[AddrStr, Instance] = {}
        # known replacements are represented as the reference of the instance
        # to be replaced, and a tuple containing the length of the ref of the
        # thing that called for that replacement, and the object that will replace it
        self._known_replacements: dict[AddrStr, Replacement] = {}
        self.obj_layer_getter = obj_layer_getter

        self._instance_context_stack: list[AddrStr] = []
        self._obj_context_stack: list[AddrStr] = []
        super().__init__(
            error_handler,
        )

    def get_instance_tree(self, addr: AddrStr) -> Instance:
        """Return an instance object represented by the given address."""
        if addr not in self._output_cache:
            obj_layer = self.obj_layer_getter(addr)
            obj = self.make_instance(addr.ref, obj_layer)
            assert isinstance(obj, Instance)
            self._output_cache[addr] = obj
        return self._output_cache[addr]

    @contextmanager
    def enter_instance(self, instance: AddrStr):
        self._instance_context_stack.append(instance)
        try:
            yield
        finally:
            self._instance_context_stack.pop()

    @contextmanager
    def enter_obj(self, instance: AddrStr):
        self._obj_context_stack.append(instance)
        try:
            yield
        finally:
            self._obj_context_stack.pop()

    def apply_replacements_from_objs(self, objs: Iterable[ObjectLayer]) -> Iterable[AddrStr]:
        """
        Apply the replacements defined in the given objects,
        returning which replacements were applied
        """
        commanded_replacements = []

        for obj in objs:
            for ref, replacement in obj.obj_def.replacements.items():
                to_be_replaced_addr = add_instance(
                    self._instance_context_stack[-1],
                    ".".join(ref)
                )
                if to_be_replaced_addr not in self._known_replacements:
                    self._known_replacements[to_be_replaced_addr] = replacement
                    commanded_replacements.append(to_be_replaced_addr)

        return commanded_replacements

    def make_instance(self, new_ref: Ref, super: ObjectLayer) -> Instance:
        """Create an instance from a reference and a super object layer."""
        supers = list(recurse(lambda x: x.super, super))

        commanded_replacements = self.apply_replacements_from_objs(supers)

        with self.enter_instance(new_ref, super):
            # FIXME: can we make this functional easily?
            # FIXME: this should deal with name collisions and type collisions
            all_internal_items: list[KeyOptItem] = []
            for super in reversed(supers):
                if super.src_ctx is None:
                    # FIXME: this is currently the case for the builtins
                    continue
                internal_items = self.visitBlock(super.src_ctx)
                all_internal_items.extend(internal_items)

        for ref in commanded_replacements:
            self._known_replacements.pop(ref)

        internal_by_type = KeyOptMap(all_internal_items).map_items_by_type(
            [Instance, LinkDef, (str, int, float, bool)]
        )

        children: dict[Ref, Instance] = {k[0]: v for k, v in internal_by_type[Instance]}

        def _lookup_item_in_children(
            _children: dict[Ref, Instance], ref: Ref
        ) -> Instance:
            if ref[0] not in _children:
                raise errors.AtoError(f"Unknown reference: {ref}")
            if len(ref) == 1:
                return _children[ref[0]]

            sub_children = _children[ref[0]].children
            return _lookup_item_in_children(sub_children, ref[1:])

        # make links
        links: list[Link] = []
        for _, link_def in internal_by_type[LinkDef]:
            assert isinstance(link_def, LinkDef)
            source_instance = _lookup_item_in_children(children, link_def.source)
            target_instance = _lookup_item_in_children(children, link_def.target)
            link = Link(
                src_ctx=link_def.src_ctx,
                parent=source_instance,
                source=source_instance,
                target=target_instance,
            )
            links.append(link)

        for key, value in internal_by_type[(str, int, float, bool)]:
            # TODO: make sure key exists?
            to_override_in = _lookup_item_in_children(children, key[:-1])
            key_name = key[-1]
            to_override_in.override_data[key_name] = value
            to_override_in._override_location[key_name] = super

        # we don't yet know about any of the overrides we may encounter
        # we pre-define this variable so we can stick it in the right slot and in the chain map
        override_data: dict[str, Any] = {}
        data = ChainMap(override_data, *[s.data for s in supers])

        new_instance = Instance(
            ref=new_ref,
            supers=supers,
            children=children,
            links=links,
            data=data,
            override_data=override_data,
        )

        for link in links:
            link.parent = new_instance

        self._output_cache[new_ref] = new_instance

        return new_instance

    def visitBlockdef(self, ctx: ap.BlockdefContext) -> _Sentinel:
        """Don't go down blockdefs, they're just for defining objects."""
        return NOTHING

    def visitAssign_stmt(self, ctx: ap.Assign_stmtContext) -> KeyOptMap:
        assigned_ref = self.visitName_or_attr(ctx.name_or_attr())
        if len(assigned_ref) == 1:
            # we've already dealt with this!
            return KeyOptMap(())

        assigned_name: str = assigned_ref[-1]

        assignable_ctx = ctx.assignable()
        assert isinstance(assignable_ctx, ap.AssignableContext)

        # handle new statements
        if assignable_ctx.new_stmt():
            new_stmt = assignable_ctx.new_stmt()
            assert isinstance(new_stmt, ap.New_stmtContext)
            if len(assigned_ref) != 1:
                raise errors.AtoError(
                    "Cannot assign a new object to a multi-part reference"
                )

            new_class_ref = self.visitName_or_attr(new_stmt.name_or_attr())

            object_context = self.

            # FIXME: this is a giant fucking mess
            new_ref = Ref(self._ref_stack[-1] + assigned_ref)

            if new_ref in self._known_replacements:
                super_addr = lookup_obj_in_closure(
                    object_context.obj_def,
                    self._known_replacements[new_ref].new_super_ref,
                )
                actual_super = self.obj_layer_getter(super_addr)
            else:
                try:
                    new_class_addr = lookup_obj_in_closure(
                        object_context.obj_def,
                        new_class_ref
                    )
                except KeyError as ex:
                    raise errors.AtoKeyError.from_ctx(
                        f"Couldn't find ref {new_class_ref}", ctx
                    ) from ex
                actual_super = self.obj_layer_getter(new_class_addr)

            new_instance = self.make_instance(new_ref, actual_super)

            return KeyOptMap.from_kv(assigned_ref, new_instance)

        assigned_value = self.visitAssignable(ctx.assignable())
        return KeyOptMap.from_kv(assigned_ref, assigned_value)

    def visitTotally_an_integer(self, ctx: ap.Totally_an_integerContext) -> int:
        text = ctx.getText()
        try:
            return int(text)
        except ValueError:
            raise errors.AtoTypeError.from_ctx(  # pylint: disable=raise-missing-from
                f"Expected an integer, but got {text}", ctx
            )

    def visitPindef_stmt(self, ctx: ap.Pindef_stmtContext) -> KeyOptMap:
        ref = self.visit_ref_helper(ctx.totally_an_integer() or ctx.name())
        assert len(ref) == 1  # TODO: unwrap these refs, now they're always one long
        if not ref:
            raise errors.AtoError("Pins must have a name")

        override_data: dict[str, Any] = {}

        pin = Instance(
            src_ctx=ctx,
            ref=Ref(self._ref_stack[-1] + ref),
            supers=(PIN,),
            children={},
            links=[],
            data=override_data,  # FIXME: this should be a chain map
            override_data=override_data,
        )

        return KeyOptMap.from_kv(ref, pin)

    def visitSignaldef_stmt(self, ctx: ap.Signaldef_stmtContext) -> KeyOptMap:
        ref = self.visit_ref_helper(ctx.name())
        if not ref:
            raise errors.AtoError("Signals must have a name")

        override_data: dict[str, Any] = {}

        signal = Instance(
            src_ctx=ctx,
            ref=Ref(self._ref_stack[-1] + ref),
            supers=(SIGNAL,),
            children={},
            links=[],
            data=override_data,  # FIXME: this should be a chain map
            override_data=override_data,
        )

        return KeyOptMap.from_kv(ref, signal)

    def visitConnect_stmt(self, ctx: ap.Connect_stmtContext) -> KeyOptMap:
        """
        Connect interfaces together
        """
        source_name, source = self.visitConnectable(ctx.connectable(0))
        target_name, target = self.visitConnectable(ctx.connectable(1))

        returns = [
            KeyOptItem.from_kv(
                None,
                LinkDef(source_name, target_name, src_ctx=ctx),
            )
        ]

        # If the connect statement is also used to instantiate
        # an element, add it to the return tuple
        if source:
            returns.append(source)

        if target:
            returns.append(target)

        return KeyOptMap(returns)

    def visitConnectable(
        self, ctx: ap.ConnectableContext
    ) -> tuple[Ref, Optional[KeyOptItem]]:
        if ctx.name_or_attr():
            # Returns a tuple
            return self.visit_ref_helper(ctx.name_or_attr()), None
        elif ctx.numerical_pin_ref():
            return self.visit_ref_helper(ctx.numerical_pin_ref()), None
        elif ctx.pindef_stmt() or ctx.signaldef_stmt():
            connectable: KeyOptMap = self.visitChildren(ctx)
            # return the object's ref and the created object itself
            ref = connectable[0][0]
            assert ref is not None
            return ref, connectable[0]
        else:
            raise ValueError("Unexpected context in visitConnectable")

    def visitSimple_stmt(self, ctx: ap.Simple_stmtContext) -> KeyOptMap:
        """We have to be selective here to deal with the ignored children properly."""
        if (
            ctx.assign_stmt()
            or ctx.connect_stmt()
            or ctx.pindef_stmt()
            or ctx.signaldef_stmt()
        ):
            return super().visitSimple_stmt(ctx)

        return KeyOptMap.empty()
