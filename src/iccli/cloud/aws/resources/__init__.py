# Copyright 2019 Farzad Senart and Lionel Suss. All rights reserved.
#
# This file is part of IC CLI.
#
# IC CLI is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# IC CLI is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with IC CLI. If not, see <https://www.gnu.org/licenses/>.

import importlib.resources
import inspect
import json
import numbers
import re
from collections import defaultdict
from contextlib import suppress
from copy import deepcopy
from datetime import date, datetime
from functools import lru_cache, partial, reduce
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    MutableMapping,
    NoReturn,
    Optional,
    Set,
    Tuple,
    Type,
    Union,
    cast,
)

from mypy_extensions import KwArg, VarArg

from ....core import resource as base
from .. import types

TRANS: Mapping[str, str] = json.loads(
    importlib.resources.read_text(__package__, "trans.json")
)


class Resource(base.Resource):
    # pylint: disable=too-many-instance-attributes

    def __init__(
        self,
        name: str,
        vnd: str,
        svc: str,
        com: str,
        typ: str,
        attrs: Mapping[str, Union[Type[types.Ref], Type[types.Attr]]],
        check: "ObjectChecker",
        props: Mapping[str, Any],
    ):
        super().__init__(name)
        self._vnd = vnd  # aws
        self._svc = svc  # cloudformation
        self._com = com  # wait_condition_handle
        self._type = typ  # AWS::CloudFormation::WaitConditionHandle
        self._attrs = {k: v(self.id) for k, v in attrs.items()}
        self._check = check
        self._raw_props = cast(Mapping[str, Any], {})
        self._props = cast(Mapping[str, Any], {})
        self.props = props
        self._reqs = cast(List[base.Resource], [])
        self._deletion = cast(Optional[str], None)

    def __getitem__(self, item: str):
        if self._type == "AWS::CloudFormation::CustomResource":
            if item not in self._attrs:
                return types.Attr[types.Str, item](self.id)  # type: ignore
        return self._attrs[item]

    @property
    def lineage(self) -> Tuple[str, ...]:
        return super().lineage + (self._vnd, self._svc, self._com)

    @property
    def type(self) -> str:
        return ".".join((self._vnd, self._svc, self._com))

    @property
    def props(self) -> Mapping[str, Any]:
        return deepcopy(self._raw_props)

    @props.setter
    def props(self, val: Mapping[str, Any]):
        try:
            self._props = self._check(val)
            self._raw_props = val
        except Exception as exc:
            raise TypeError(f"{'.'.join(self.lineage)}: {str(exc)}") from None

    def require(self, *deps: "Resource"):
        self._reqs.extend(deps)

    def deletion(self, policy: str):
        policies = {"delete", "retain", "snapshot"}
        if policy not in policies:
            raise ValueError(
                f"invalid deletion policy {policy!r}, expected one of {policies}"
            )
        self._deletion = policy

    # python/myp#220
    deletion = property(fset=deletion)  # type: ignore


Spec = Mapping[str, Mapping[str, Mapping[str, Callable[[VarArg(), KwArg()], Resource]]]]


@lru_cache(maxsize=None)
def load(region: str) -> Spec:
    specs = json.loads(importlib.resources.read_text(__package__, f"{region}.json"))
    rescs: MutableMapping[
        str,
        MutableMapping[
            str, MutableMapping[str, Callable[[VarArg(), KwArg()], Resource]]
        ],
    ] = {}
    for typ, spec in specs["ResourceTypes"].items():
        rvnd, rsvc, rcom = typ.split("::")
        vnd, svc, com = TRANS[rvnd], TRANS[rsvc], TRANS[rcom]
        svc = "lambda_" if svc == "lambda" else svc
        attrs: MutableMapping[str, Union[Type[types.Ref], Type[types.Attr]]] = {
            TRANS[a]: _resolve_attr(a, s) for a, s in spec.get("Attributes", {}).items()
        }
        attrs["ref"] = types.Ref
        check = _resolve_check(typ, spec, specs)

        def bake(vnd_, svc_, com_, typ_, attrs_, check_):
            def factory(*args, **kwargs) -> Resource:
                name = next(iter(args), "")
                # props = dict(kwargs) if kwargs else None
                res = Resource(
                    name, vnd_, svc_, com_, typ_, attrs_, check_, dict(kwargs)
                )
                return res

            factory.__module__ = ".".join(["ic", vnd_, svc_])
            factory.__name__ = com_
            factory.__qualname__ = com_
            sigs = [
                inspect.Parameter(p, inspect.Parameter.KEYWORD_ONLY)
                for p in check_.items
            ]
            # python/myp#5958
            factory.__signature__ = inspect.Signature(sigs)  # type: ignore
            return factory

        resc = bake(vnd, svc, com, typ, attrs, check)
        rescs.setdefault(vnd, {}).setdefault(svc, {})[com] = resc
    return rescs


TYPES = {
    "Boolean": types.Bool,
    "Integer": types.Int,
    "Json": types.Str,
    "List": types.List,
    "String": types.Str,
}


def _resolve_attr(name: str, spec) -> Type[types.Attr]:
    ptrs = ("PrimitiveType", "Type", "PrimitiveItemType")
    raws = [spec.get(t) for t in ptrs]
    typs = [TYPES[t] for t in raws if t is not None]
    typ = reduce(lambda sub, par: par[sub], reversed(typs))  # type: ignore
    return types.Attr[typ, name]  # type: ignore


def _check_any(val: Any):
    return val


def _check_type(typs: Tuple[Type, ...], val: Any):
    if val is None:
        return None
    if not isinstance(val, typs):
        exp = " or ".join([repr(t.__name__) for t in typs])
        raise TypeError(f"incompatible type {type(val).__name__!r}, expected {exp}")
    return val


def _check_number(val: Any):
    if val is None:
        return None
    valf = val
    if isinstance(val, str):
        with suppress(ValueError):
            valf = float(val)
    _check_type((numbers.Number, types.Int), valf)
    return val


def _check_timestamp(val: Any):
    if val is None:
        return None
    _check_type((date, datetime), val)
    if val.tzinfo is None:
        raise TypeError("missing timezone information")
    return val


def _check_list(items: Callable[[Any], NoReturn], val: Any):
    if val is None:
        return None
    _check_type((list, types.List), val)
    if isinstance(val, types.Opaque):
        return val
    res: Any = []
    if not isinstance(val, types.Opaque):
        for i, item in enumerate(val):
            if item is not None:
                try:
                    res.append(items(item))
                except Exception as exc:
                    raise TypeError(f"[{i}]: {str(exc)}") from None
    return res


def _check_map(items: Callable[[Any], NoReturn], val: Any) -> Any:
    if val is None:
        return None
    _check_type((dict,), val)
    res: Any = {}
    for prop, item in val.items():
        if item is not None:
            try:
                res[prop] = items(item)
            except Exception as exc:
                raise TypeError(f"{prop}: {str(exc)}") from None
    return res


class ObjectChecker:
    # pylint: disable=too-few-public-methods

    items: Dict[str, Callable[[Any], NoReturn]]
    reqs: Set[str]
    trans: Mapping[str, str]

    def __init__(self, custom: bool = False):
        self.custom = custom

    def __call__(self, val: Any) -> Any:
        if val is None:
            val = {}
        else:
            _check_type((dict,), val)
        extra = set(val.keys()) - set(self.items.keys())
        if extra and not self.custom:
            raise TypeError(f"got unexpected keys: {extra!r}")
        missing = self.reqs - {k for k, v in val.items() if v is not None}
        if missing:
            raise TypeError(f"missing required keys: {missing!r}")
        res: Any = {}
        for prop, item in val.items():
            if item is not None:
                try:
                    if prop in extra:
                        if not re.fullmatch(r"[a-z0-9_]+", prop):
                            raise TypeError(
                                f"got malformed key, expected [a-z0-9_]+: {prop!r}"
                            )
                        res[prop] = item
                    else:
                        res[self.trans[prop]] = self.items[prop](item)
                except Exception as exc:
                    raise TypeError(f"{prop}: {str(exc)}") from None
        return res


CHECKS: Mapping[str, Callable] = {
    "Boolean": partial(_check_type, (bool, types.Bool)),
    "Double": _check_number,
    "Integer": _check_number,
    "Json": _check_any,
    "List": _check_list,
    "Long": _check_number,
    "Map": _check_map,
    "String": partial(_check_type, (str, types.Str)),
    "Timestamp": _check_timestamp,
}


def _resolve_check(name: str, spec, specs, cache=None) -> Callable[[Any], NoReturn]:
    com = name.split(".")[0]
    cache = cache or {}
    key = f"{com}.{name}"
    if key in cache:
        return cache[key]
    if "PrimitiveType" in spec:
        cache[key] = CHECKS[spec["PrimitiveType"]]
    elif "PrimitiveItemType" in spec:
        check = CHECKS[spec["PrimitiveItemType"]]
        cache[key] = partial(CHECKS[spec["Type"]], check)
    elif "Properties" in spec:
        cache[key] = ObjectChecker(name == "AWS::CloudFormation::CustomResource")
        cache[key].items = {
            TRANS[prop]: _resolve_check(f"{com}.{prop}", sspec, specs, cache)
            for prop, sspec in spec["Properties"].items()
        }
        cache[key].reqs = {
            TRANS[prop]
            for prop, sspec in spec["Properties"].items()
            if sspec["Required"]
        }
        cache[key].trans = {TRANS[prop]: prop for prop in spec["Properties"]}
    else:
        typ = spec.get("ItemType") or spec["Type"]
        typs = specs["PropertyTypes"]
        skey = f"{com}.{typ}"
        sspec = typs.get(skey) or typs[typ]
        check = cache.get(skey)
        if not check:
            check = _resolve_check(f"{com}.{name}", sspec, specs, cache)
        if "ItemType" in spec:
            cache[key] = partial(CHECKS[spec["Type"]], check)
        elif "Type" in spec:
            cache[key] = check
        else:
            raise NotImplementedError  # pragma: no cover
    return cache[key]
