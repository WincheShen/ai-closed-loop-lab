"""config/wheels.yaml 加载与 schema 校验。

配置只允许：选哪些步骤、什么顺序、填什么参数。不允许条件表达式。
校验失败（引用未注册的步骤、非法结构）→ WheelConfigError，
scheduler 启动时应拒绝启动并告警，而不是运行时报错。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.wheel import registry
from src.wheel.protocol import PersonaPolicy

_WHEELS_YAML = Path("config/wheels.yaml")
_VALID_KINDS = ("decision", "funnel")
_VALID_MODES = ("shadow", "live", "off")


class WheelConfigError(Exception):
    pass


@dataclass(frozen=True)
class WheelDef:
    name: str
    kind: str
    data: tuple[str, ...] = ()
    veto: tuple[str, ...] = ()
    propose: tuple[str, ...] = ()
    resolve: str = "priority"
    modify: tuple[str, ...] = ()
    steps: tuple[str, ...] = ()
    output: str = ""


@dataclass(frozen=True)
class Binding:
    event_type: str
    wheel: str = ""
    chain: tuple[str, ...] = ()
    params: dict = field(default_factory=dict)
    mode: str = "shadow"


@dataclass(frozen=True)
class PersonaWheelConfig:
    persona_id: str
    kind: str
    bindings: dict[str, Binding]


@dataclass(frozen=True)
class WheelConfig:
    wheels: dict[str, WheelDef]
    personas: dict[str, PersonaWheelConfig]

    def binding_for(self, persona_id: str | None, event_type: str) -> Binding | None:
        pc = self.personas.get(persona_id or "")
        if pc is None:
            return None
        return pc.bindings.get(event_type)

    def persona_policy(self, persona_id: str, binding: Binding | None = None) -> PersonaPolicy:
        pc = self.personas.get(persona_id)
        return PersonaPolicy(
            persona_id=persona_id,
            kind=(pc.kind if pc else "short"),
            params=dict(binding.params) if binding else {},
        )


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise WheelConfigError(msg)


def _as_str_list(value: Any, where: str) -> list[str]:
    if value is None:
        return []
    _require(isinstance(value, list), f"{where} 必须是列表，得到 {type(value).__name__}")
    for v in value:
        _require(isinstance(v, str), f"{where} 元素必须是字符串: {v!r}")
    return list(value)


def _parse_steps(names: list[str], where: str) -> list[tuple[str, dict]]:
    """支持 'name' 或 'name(arg=v, ...)' 两种写法。"""
    parsed = []
    for item in names:
        if "(" in item:
            _require(item.endswith(")"), f"{where}: 非法步骤写法 {item!r}")
            name, argstr = item[:-1].split("(", 1)
            args: dict[str, Any] = {}
            for part in argstr.split(","):
                part = part.strip()
                if not part:
                    continue
                _require("=" in part, f"{where}: 非法参数 {part!r} in {item!r}")
                k, v = part.split("=", 1)
                k, v = k.strip(), v.strip()
                try:
                    args[k] = float(v) if "." in v or "e" in v.lower() else int(v)
                except ValueError:
                    args[k] = v
            parsed.append((name.strip(), args))
        else:
            parsed.append((item, {}))
    return parsed


def _declared_params(factor_name: str) -> set[str]:
    """Factor 声明接受的参数名；未声明一律不接受（防止参数名写错被静默忽略）。"""
    try:
        fac = registry.factor(factor_name)
    except KeyError:
        return set()
    return set(getattr(fac, "param_keys", ()) or ())


def load_wheel_config(path: Path | str | None = None) -> WheelConfig:
    """加载并校验 wheels.yaml。所有引用必须在 registry 中已注册。"""
    yaml_path = Path(path) if path else _WHEELS_YAML
    _require(yaml_path.exists(), f"wheels.yaml 不存在: {yaml_path}")

    try:
        data = yaml.safe_load(yaml_path.read_bytes()) or {}
    except yaml.YAMLError as e:
        raise WheelConfigError(f"wheels.yaml 解析失败: {e}") from e

    raw_wheels = data.get("wheels") or {}
    _require(isinstance(raw_wheels, dict) and raw_wheels, "wheels.yaml 缺少 wheels 定义")

    wheels: dict[str, WheelDef] = {}
    for wname, wdef in raw_wheels.items():
        where = f"wheel[{wname}]"
        _require(isinstance(wdef, dict), f"{where} 必须是映射")
        kind = wdef.get("kind")
        _require(kind in _VALID_KINDS, f"{where}.kind 非法: {kind!r}")

        data_steps = _as_str_list(wdef.get("data"), f"{where}.data")
        veto = _as_str_list(wdef.get("veto"), f"{where}.veto")
        propose = _as_str_list(wdef.get("propose"), f"{where}.propose")
        modify = _as_str_list(wdef.get("modify"), f"{where}.modify")
        steps = _as_str_list(wdef.get("steps"), f"{where}.steps")
        resolve = wdef.get("resolve", "priority")
        output = wdef.get("output", "")

        for name in data_steps:
            _require(name in registry.data_step_names(), f"{where}.data 未注册: {name}")
        for name in veto:
            _require(name in registry.factor_names(), f"{where}.veto 未注册: {name}")
            _require(registry.factor(name).stage == "veto", f"{where}.veto {name} stage 不是 veto")
        for name, _args in _parse_steps(propose, f"{where}.propose"):
            _require(name in registry.factor_names(), f"{where}.propose 未注册: {name}")
            _require(registry.factor(name).stage == "propose", f"{where}.propose {name} stage 不是 propose")
            _require(
                set(_args) <= _declared_params(name),
                f"{where}.propose {name} 不接受参数: {sorted(set(_args) - _declared_params(name))}",
            )
        for name, _args in _parse_steps(modify, f"{where}.modify"):
            _require(name in registry.factor_names(), f"{where}.modify 未注册: {name}")
            _require(registry.factor(name).stage == "modify", f"{where}.modify {name} stage 不是 modify")
            _require(
                set(_args) <= _declared_params(name),
                f"{where}.modify {name} 不接受参数: {sorted(set(_args) - _declared_params(name))}",
            )
        for name, _args in _parse_steps(steps, f"{where}.steps"):
            _require(name in registry.factor_names(), f"{where}.steps 未注册: {name}")
            _require(
                set(_args) <= _declared_params(name),
                f"{where}.steps {name} 不接受参数: {sorted(set(_args) - _declared_params(name))}",
            )

        if kind == "decision":
            _require(resolve == "priority", f"{where}.resolve 仅支持 priority: {resolve!r}")
        if kind == "funnel":
            _require(steps, f"{where} funnel 必须配置 steps")

        wheels[wname] = WheelDef(
            name=wname, kind=kind,
            data=tuple(data_steps), veto=tuple(veto),
            propose=tuple(propose), resolve=resolve,
            modify=tuple(modify), steps=tuple(steps), output=output,
        )

    personas: dict[str, PersonaWheelConfig] = {}
    raw_personas = data.get("personas") or {}
    for pid, pdef in raw_personas.items():
        where = f"persona[{pid}]"
        _require(isinstance(pdef, dict), f"{where} 必须是映射")
        kind = pdef.get("kind", "short")
        _require(kind in ("short", "ultra", "value"), f"{where}.kind 非法: {kind!r}")

        bindings: dict[str, Binding] = {}
        for event_type, bdef in (pdef.get("bindings") or {}).items():
            bwhere = f"{where}.bindings[{event_type}]"
            _require(isinstance(bdef, dict), f"{bwhere} 必须是映射")
            wheel = bdef.get("wheel")
            chain = _as_str_list(bdef.get("chain"), f"{bwhere}.chain")
            mode = bdef.get("mode", "shadow")
            params = bdef.get("params") or {}
            _require(mode in _VALID_MODES, f"{bwhere}.mode 非法: {mode!r}")
            _require(isinstance(params, dict), f"{bwhere}.params 必须是映射")
            _require(bool(wheel) ^ bool(chain), f"{bwhere} 必须且只能配置 wheel 或 chain")
            if wheel:
                _require(wheel in wheels, f"{bwhere}.wheel 未定义: {wheel}")
            for wname in chain:
                _require(wname in wheels, f"{bwhere}.chain 引用未定义转盘: {wname}")
            # binding.params 只允许填写所绑转盘各 Factor 声明过的参数名
            declared: set[str] = set()
            for wname in ((wheel,) if wheel else chain):
                wdef = wheels[wname]
                for fname, _fargs in (
                    _parse_steps(list(wdef.veto) + list(wdef.propose)
                                 + list(wdef.modify) + list(wdef.steps), bwhere)
                ):
                    declared |= _declared_params(fname)
            unknown = set(params) - declared
            _require(
                not unknown,
                f"{bwhere}.params 含未声明参数 {sorted(unknown)}"
                f"（该绑定可用参数: {sorted(declared) or '无'}）",
            )
            bindings[event_type] = Binding(
                event_type=event_type, wheel=wheel or "",
                chain=tuple(chain), params=params, mode=mode,
            )

        personas[pid] = PersonaWheelConfig(persona_id=pid, kind=kind, bindings=bindings)

    return WheelConfig(wheels=wheels, personas=personas)


_cache: WheelConfig | None = None


def get_config(reload: bool = False, path: Path | str | None = None) -> WheelConfig:
    """带缓存的配置加载。"""
    global _cache
    if _cache is None or reload or path is not None:
        cfg = load_wheel_config(path)
        if path is None:
            _cache = cfg
        return cfg
    return _cache


def clear_cache() -> None:
    global _cache
    _cache = None
