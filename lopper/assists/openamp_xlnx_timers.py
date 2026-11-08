#/*
# * Copyright (C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */
"""RPU sleep and tick timers selected by the domain description.

A baremetal or FreeRTOS domain names the timers that its xiltimer library
uses::

    xlnx,sleep-timer = <&ttc3 0>;    /* baremetal and freertos */
    xlnx,tick-timer = <&ttc3 1>;     /* freertos only */

The YAML form ``xlnx,sleep-timer: [ ttc3, 0 ]`` expands to the same
property. When a domain sets neither property, the timer of its RPMsg
relation is used instead: counter 0 for sleep and counter 1 for tick.

The checks here run on the assembled tree and are keyed by the timer node's
``compatible``, so every input route gets them. The result is a C file that
defines ``XSleepTimer_Instance`` and ``XTickTimer_Instance``. xiltimer
defines both as weak variables, so the definitions select the timers for one
application without changing the BSP.
"""

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from lopper.assists.lopper_lib import node_accesses, node_reg_start_size

SLEEP = "sleep"
TICK = "tick"

ROLE_PROPERTIES = {
    SLEEP: "xlnx,sleep-timer",
    TICK: "xlnx,tick-timer",
}

ROLE_VARIABLES = {
    SLEEP: "XSleepTimer_Instance",
    TICK: "XTickTimer_Instance",
}

# Roles each os,type supports. Standalone has no tick timer.
OS_ROLES = {
    "baremetal": (SLEEP,),
    "freertos": (SLEEP, TICK),
}

# Counters used when the timer comes from the RPMsg relation.
FALLBACK_COUNTERS = {
    SLEEP: 0,
    TICK: 1,
}

RPMSG_RELATION = "openamp,rpmsg-v1"
TIMER_RELATIONS = (RPMSG_RELATION, "libmetal,ipc-v1")

TIMER_CONFIG_FILE = "amd_timer_config.c"


class TimerError(ValueError):
    """A timer reference that cannot be used."""


def ttc_counter_name(name, counter):
    """Return the name ``lop-ttc-split.dts`` gives a TTC counter node.

    The lop renames TTC *n* to *3n* and adds counters *3n+1* and *3n+2*, so
    ``psv_ttc_3`` counter 1 becomes ``psv_ttc_10``.
    """
    match = re.match(r"^(.*?)(\d+)$", name)
    if not match:
        return None
    return f"{match.group(1)}{int(match.group(2)) * 3 + counter}"


def ttc_counter(node, counter):
    """Return the base address and BSP name of a Cadence TTC counter.

    Each TTC has three counters, whose registers start at base + 4 *
    counter. ``lop-ttc-split.dts`` creates one node per counter at those
    addresses for the baremetal drivers, and xiltimer finds a counter by
    that address.
    """
    if not isinstance(counter, int) or not 0 <= counter <= 2:
        raise TimerError(f"counter {counter!r} is not valid for "
                         f"{node.abs_path}; a TTC has counters 0, 1 and 2")

    base, _ = node_reg_start_size(node)
    if base is None:
        raise TimerError(f"{node.abs_path} has no reg property")

    name = node.propval("xlnx,name", list)
    instance = None
    if name != [""] and isinstance(name[0], str):
        instance = ttc_counter_name(name[0], counter)

    return base + 4 * counter, instance


# Timer handlers keyed by compatible. A handler takes the timer node and the
# counter and returns (address, BSP instance name or None).
TIMER_HANDLERS = {
    "cdns,ttc": ttc_counter,
}


@dataclass
class TimerChoice:
    """One resolved timer role for a domain."""
    role: str
    node: object
    counter: int
    address: int
    instance: Optional[str]
    source: str


@dataclass
class Claim:
    """A reason a timer node is in use."""
    domain: object
    description: str
    # "access", "property", "rpmsg-remote" or "relation"
    kind: str


@dataclass
class DomainTimers:
    """Timers resolved for one domain."""
    domain: object
    choices: Dict[str, TimerChoice] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    fallback: bool = False


def _node_name(node):
    return node.label or node.name


def domain_os(domain):
    """Return the domain's ``os,type`` or None."""
    value = domain.propval("os,type", list)
    if value == [""] or not isinstance(value[0], str):
        return None
    return value[0]


def domain_nodes(tree):
    """Return every domain node: nodes under /domains that have cpus."""
    try:
        domains = tree["/domains"]
    except Exception:
        return []
    return [node for node in domains.subnodes()
            if node is not domains and node.propval("cpus", list) != [""]]


def _domain_relations(domain):
    """Yield (relation group compatible, relation) for a domain."""
    for d2d in domain.subnodes(children_only=True):
        if d2d.parent is not domain or d2d.name != "domain-to-domain":
            continue
        for group in d2d.subnodes(children_only=True):
            if group.parent is not d2d:
                continue
            compatible = group.propval("compatible", list)
            if compatible == [""] or compatible[0] not in TIMER_RELATIONS:
                continue
            for relation in group.subnodes(children_only=True):
                if relation.parent is group:
                    yield compatible[0], relation


def _relation_timers(relation):
    value = relation.propval("timer", list)
    if value == [""]:
        return []
    return [phandle for phandle in value if isinstance(phandle, int)]


def _explicit_properties(domain):
    return {role: prop for role, prop in ROLE_PROPERTIES.items()
            if domain.propval(prop, list) != [""]}


def timer_claims(tree):
    """Map timer node paths to the domains and relations that use them.

    Uses are a domain ``access`` entry, a domain timer property, and the
    ``timer`` of an RPMsg or Libmetal relation. The remote side of an RPMsg
    relation (the child that names ``host``) is recorded separately, because
    it is the remote's own timer and the fallback source.
    """
    claims = {}

    def add(node, claim):
        if node is not None:
            claims.setdefault(node.abs_path, []).append(claim)

    for domain in domain_nodes(tree):
        name = domain.name
        for node in node_accesses(tree, domain):
            add(node, Claim(domain, f"{name} access", "access"))

        for role, prop in _explicit_properties(domain).items():
            value = domain.propval(prop, list)
            if isinstance(value[0], int):
                add(tree.pnode(value[0]), Claim(domain, f"{name} {prop}",
                                                "property"))

        for compatible, relation in _domain_relations(domain):
            kind = "relation"
            if (compatible == RPMSG_RELATION
                    and relation.propval("host", list) != [""]):
                kind = "rpmsg-remote"
            for phandle in _relation_timers(relation):
                add(tree.pnode(phandle),
                    Claim(domain, f"{relation.abs_path} timer", kind))

    return claims


def _conflicts(domain, node, claims):
    """Return descriptions of uses of ``node`` that exclude ``domain``."""
    conflicts = []
    for claim in claims.get(node.abs_path, []):
        if claim.domain is domain and claim.kind != "relation":
            continue
        conflicts.append(claim.description)
    return conflicts


def _check_timer(tree, domain, node, counter, claims):
    """Validate one timer reference and return (address, instance)."""
    compatible = node.propval("compatible", list)
    if "uio" in compatible:
        raise TimerError(
            f"{node.abs_path} is a Linux UIO device (compatible \"uio\"), "
            "shared with Linux through a Libmetal relation; it cannot be a "
            "sleep or tick timer")

    handler = next((TIMER_HANDLERS[c] for c in compatible
                    if c in TIMER_HANDLERS), None)
    if handler is None:
        raise TimerError(
            f"{node.abs_path}: no timer handler for compatible "
            f"{', '.join(compatible) or '(none)'}; supported: "
            f"{', '.join(sorted(TIMER_HANDLERS))}")

    address, instance = handler(node, counter)

    if domain.propval("access", list) != [""]:
        if node not in node_accesses(tree, domain):
            raise TimerError(f"{domain.name} has an access list that does "
                             f"not include {node.abs_path}")

    conflicts = _conflicts(domain, node, claims)
    if conflicts:
        raise TimerError(f"{node.abs_path} is also used by: "
                         f"{'; '.join(conflicts)}")

    return address, instance


def _rpmsg_fallback_timer(tree, domain, result):
    """Return the timer node of the domain's own RPMsg relation, or None."""
    timers = []
    for compatible, relation in _domain_relations(domain):
        if (compatible == RPMSG_RELATION
                and relation.propval("host", list) != [""]):
            timers.extend(_relation_timers(relation))
    if not timers:
        return None
    if len(timers) > 1:
        result.warnings.append(
            f"{domain.name}: RPMsg relation names {len(timers)} timers; "
            "using the first for sleep and tick")
    node = tree.pnode(timers[0])
    if node is None:
        result.warnings.append(f"{domain.name}: RPMsg relation timer "
                               f"{timers[0]:#x} does not resolve")
    return node


def resolve_domain_timers(tree, domain, claims=None):
    """Resolve and check the sleep and tick timers of one domain.

    Explicit properties win. Without them, the domain's RPMsg relation timer
    supplies counter 0 for sleep and counter 1 for tick. A problem with an
    explicit property is an error. A problem with the fallback is a warning
    and no timer is chosen, so YAMLs that predate the properties keep the
    BSP defaults.

    Returns:
        DomainTimers: choices, errors and warnings.
    """
    if claims is None:
        claims = timer_claims(tree)

    result = DomainTimers(domain)
    os_type = domain_os(domain)
    supported = OS_ROLES.get(os_type, ())
    explicit = _explicit_properties(domain)

    for role, prop in explicit.items():
        if role not in supported:
            result.errors.append(
                f"{domain.name}: {prop} is not supported for os,type "
                f"'{os_type}'")

    if result.errors:
        return result

    references = {}
    if explicit:
        for role, prop in explicit.items():
            if role not in supported:
                continue
            value = domain.propval(prop, list)
            if len(value) != 2:
                result.errors.append(
                    f"{domain.name}: {prop} must be <&timer counter>")
                continue
            if not isinstance(value[0], int):
                result.errors.append(
                    f"{domain.name}: {prop} names unknown timer "
                    f"'{value[0]}'")
                continue
            node = tree.pnode(value[0])
            if node is None:
                result.errors.append(
                    f"{domain.name}: {prop} references phandle "
                    f"{value[0]:#x}, which does not resolve")
                continue
            references[role] = (node, value[1],
                                f"{prop}: {_node_name(node)} counter "
                                f"{value[1]}")
    elif supported:
        node = _rpmsg_fallback_timer(tree, domain, result)
        if node is not None:
            result.fallback = True
            for role in supported:
                counter = FALLBACK_COUNTERS[role]
                references[role] = (
                    node, counter,
                    f"RPMsg relation timer: {_node_name(node)} counter "
                    f"{counter}")

    problems = []
    for role, (node, counter, source) in references.items():
        try:
            address, instance = _check_timer(tree, domain, node, counter,
                                             claims)
        except TimerError as exc:
            problems.append(f"{domain.name}: {ROLE_PROPERTIES[role]}: {exc}")
            continue
        result.choices[role] = TimerChoice(role, node, counter, address,
                                           instance, source)

    sleep = result.choices.get(SLEEP)
    tick = result.choices.get(TICK)
    if sleep and tick and sleep.address == tick.address:
        problems.append(
            f"{domain.name}: sleep and tick timers both use "
            f"{_node_name(sleep.node)} counter {sleep.counter}; select "
            "different counters")

    if result.fallback and problems:
        result.warnings.extend(
            f"{problem}; not using the RPMsg relation timer, the BSP "
            "timers apply" for problem in problems)
        result.choices = {}
    else:
        result.errors.extend(problems)
        if problems:
            result.choices = {}

    return result


def timer_node_at(tree, address):
    """Return the timer node whose first reg region contains ``address``."""
    for node in tree["/"].subnodes():
        if not node.name.startswith("timer@"):
            continue
        base, size = node_reg_start_size(node)
        if base is not None and size and base <= address < base + size:
            return node
    return None


def bsp_timer_warnings(tree, result, bsp_timers, claims=None):
    """Warn when a BSP default timer is used elsewhere.

    Applies to each role the domain leaves to the BSP. ``bsp_timers`` maps a
    role to the address the BSP uses for it.
    """
    if claims is None:
        claims = timer_claims(tree)

    domain = result.domain
    warnings = []
    for role, address in (bsp_timers or {}).items():
        if address is None or role in result.choices:
            continue
        node = timer_node_at(tree, address)
        if node is None:
            continue
        uses = _conflicts(domain, node, claims)
        if "uio" in node.propval("compatible", list):
            uses.append("Linux UIO")
        if uses:
            warnings.append(
                f"{domain.name}: the BSP {role} timer {address:#x} "
                f"({_node_name(node)}) is also used by: {'; '.join(uses)}; "
                f"set {ROLE_PROPERTIES[role]} in the domain to select "
                "another timer")
    return warnings


def render_timer_config(result):
    """Return the C source that selects the domain's timers."""
    lines = [
        f"/* {TIMER_CONFIG_FILE} - generated by Lopper from the domain "
        "YAML. Do not edit. */",
        "",
        f"/* Domain {result.domain.name}. These definitions override the "
        "weak",
        " * defaults in libxiltimer for this application only. */",
        "#include \"xil_types.h\"",
        "",
    ]
    for role in (SLEEP, TICK):
        choice = result.choices.get(role)
        if choice is None:
            continue
        comment = choice.source
        if choice.instance:
            comment += f" ({choice.instance})"
        lines.append(f"/* {comment} */")
        lines.append(f"UINTPTR {ROLE_VARIABLES[role]} = "
                     f"{choice.address:#x}U;")
    lines.append("")
    return "\n".join(lines)
