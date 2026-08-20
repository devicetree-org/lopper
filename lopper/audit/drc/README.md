# DRC rule catalog

Design Rule Checks, expressed as **data**. Every `*.yaml` in this directory is
loaded automatically.

Each rule names one of a fixed set of checks -- "this property must be
present", "these ranges must not overlap" -- and gives it arguments. Writing a
new rule against a check that already exists needs no code. The checks
themselves, and the routines that pull values out of a node for them, are in
`../checks.py`.

One file per category, so a category can be reviewed, extended or disabled on
its own:

| File                    | Covers                                            |
| ----------------------- | ------------------------------------------------- |
| `domains-baseline.yaml` | One domain: definition and integrity              |
| `cross-domain.yaml`     | Between domains: CPUs, memory, devices, hierarchy |

More categories will be added over time -- checks particular to an operating
system, to inter-domain communication, or to a device family. Each becomes its
own file here.

## How rules are found

There is no index, and no registration step: **adding a `.yaml` file to this
directory is the whole act of adding rules**. The first time checks are asked
for, every `*.yaml` here is read and parsed.

```
a run with -W drc
  -> the audit framework asks for the rules, once per run
       -> reads  <wherever lopper is installed>/lopper/audit/drc/*.yaml
          in sorted order
```

Four consequences worth knowing:

- **This directory travels with the installed lopper.** It is found beside the
  code that reads it, not in the directory you happen to run lopper from, so a
  `pip install`ed lopper and a source tree behave the same. That also means it
  has to be listed in `MANIFEST.in`, or an install ends up without the
  directory at all: no rules load, nothing is reported, and the run looks
  healthy.
- **Nothing is read unless checks are enabled**, and then only once per run.
- **Files are read in sorted order**, so a run is repeatable. It makes no
  difference today, because rules do not interact; it will if two rules are
  ever allowed to share an id.
- **`--drc <dir>` reads a directory the same way**, so one you supply behaves
  exactly like this one.

## Running them

DRC rules are **opt-in** and are deliberately *not* part of the `-W all` sweep
while the catalog is incomplete:

```bash
lopper -f -W drc_all           system.dts out.dts   # every enabled rule
lopper -f -W drc:domain-id-present   system.dts out.dts   # one rule, or one context
lopper -f --drc ./my-rules.yaml -W drc_all system.dts out.dts
```

`--drc` takes a file or a directory and may be repeated. Rules may also travel
in the tree itself, under `/__assertions__`.

## Selecting domains

`/domains` holds more than domains -- grouping nodes, memory and carveout
nodes, relation nodes, and plain children such as `chosen` -- and domains
nest, so neither depth nor position identifies one.

- **strict**: `"/domains/.*:compatible:openamp,domain-v1"`. Selector values
  match a whole list element, so this excludes single-string variants like
  `openamp,domain-v1,devices`.
- **candidate**: a node is taken to be a domain because it carries `cpus` or
  `os,type`, with the known non-domain kinds excluded. Used by the rules that
  check whether a domain is *well formed*, and which therefore cannot select on
  the property they are checking. Domains are not only produced by the YAML
  expansion (which generates `compatible`); they may be hand written or
  emitted by another tool, and those are the cases those rules exist to catch.

## How a rule is written

```yaml
drc:
  - id: my-check        # stable id; also the -W drc:<id> handle
    severity: error        # error | warning | info | block
    phase: post-yaml       # early | post-yaml | post-processing
    select: [ ... ]        # terms: "path[:prop[:val]]" or {check, params, negate}
    check: <handler>       # a leaf rule runs a handler...
    params: { ... }
    message: "..."
```

A rule with `rules:` instead of `check:` is a **context**. It selects nodes,
and the rules it holds run **once for each node selected**, with that node as
the current node -- so a context that matches nothing runs nothing. That is
what makes a nested rule conditional; there is no `if`/`when` keyword. Rules
side by side under one context all have to hold.

A context is a selection rather than an assertion, so it is never itself
reported: only the checks beneath it are, and its `severity` is just a default
they inherit and may override.

(The same idea as a Schematron `rule`/`context`, if that is familiar: a
selection evaluated once per matched node, with the assertions inside it
written relative to that node.)

A rule that compares nodes *with each other* -- rather than checking each on
its own -- uses `group-by` and `collect` in place of `select`. `group-by` says
what forms a group (one per matching node, or explicitly named groups) and
`collect` says which values to pull out of each for comparison.

A context may carry a `guard` -- a selection plus a `count` constraint -- so that
its rules run only when something is true of the system as a whole, such as
there being no hypervisor domain.

A selector term is one of three things:

- `"path[:prop[:val]]"` -- match by path, by the presence of a property, or by
  its value.
- `{check: ..., params: ..., negate: ...}` -- a **predicate**: keep the nodes an
  ordinary check handler accepts. This is how a rule tests something computed,
  such as a bit being set, which no amount of text matching can express.
- `{relative: <relation>}` -- reach a node **from the one the enclosing context
  bound**: `parent-domain`, `ancestor-domain`, `guests`, `cluster`, or any
  reference-valued property name.

A term carrying a path accumulates (OR); one without refines what has been
selected so far (AND).

## When a rule runs

`phase` says at what point in a lopper run a rule is evaluated. It matters
because it decides what the rule can rely on having happened.

- **`early`** -- the tree is loaded and resolved, but YAML inputs have not been
  expanded, so domains written in YAML do not exist yet. For checks about the
  tree as it was read.
- **`post-yaml`** -- YAML has been expanded, so domains and their properties
  are present. For checks about a single domain being well formed.
- **`post-processing`** -- everything has run and been merged. For anything
  comparing domains with each other, or a domain against the rest of the
  system. This is the default.

`pre-assist` and `post-assist` are accepted, but there are no assist hooks yet
and they currently behave exactly like `post-processing`. Do not rely on them
running around an assist.

Most single-domain checks belong in `post-yaml` and everything cross-domain in
`post-processing`. A rule placed too early does not fail -- it finds nothing,
which is worse, so prefer the later phase when unsure.

## How a failure is reported

`severity` says what a failure means. It does **not** change whether the check
runs.

- **`error`** -- counted as an error. Reported as a warning normally, as an
  error under `--werror`, which is then fatal. The default.
- **`warning`** -- reported, never fatal.
- **`info`** -- reported for information; not counted.
- **`block`** -- reported, and lopper **stops immediately**, with or without
  `--werror`. For a configuration that must not be allowed to proceed.

On a context, `severity` is only a default handed to the rules beneath it, which
may each override it. A context is never itself reported.

## The checks

What a rule can name in `check:`. Handlers marked *compare* work across a
group and need `group-by` + `collect` instead of `select`.

Checking one node at a time:

- **`required`** -- the named properties are present. `properties`
- **`const`** -- a property equals a value exactly. `property`, `value`
- **`enum`** -- a property's value is one of a set. `property`, `values`
- **`compatible-contains`** -- `compatible` includes a value, matched whole.
  `token`
- **`bitmask`** -- a numeric cell satisfies a bit or range test. `property`,
  `field` or `index`, and one of `bit`+`set`, `mask`, `equals`, `values`
- **`ref-exists`** -- a path, label or alias in a property resolves to a node.
  `property`
- **`ref-valid`** -- phandle-valued properties point at nodes that exist.
  `properties`
- **`phandle-type`** -- what a reference points at is the right kind of node.
  `property`, `compatible`, `index`, `stride`
- **`subset-of`** -- what this node holds is contained in what a related node
  holds. `of`, `relative`, `kind`
- **`acyclic`** -- following a reference property never returns to the start.
  `edge`
- **`count`** -- how many nodes were selected. `min`, `max`, `exact`
- **`is-node`** -- this node is the one the enclosing context bound. Mainly
  useful negated, as a selector term, to mean "every node except this one".
  `equals`

Comparing nodes with each other. These need `group-by` and `collect`:

- **`exclusive-across`** -- no collected value appears in more than one group.
  `unless-flag`, `ignore-nested`
- **`no-overlap`** -- no collected range intersects a range in another group.
  `unless-flag`, `ignore-nested`
- **`contained-in`** -- every range lies inside one of the ranges of a named
  group. `container`

`unless-flag` names a flag that excuses a clash when *every* group involved
sets it -- how a deliberately shared device or memory region is allowed.
`ignore-nested` (on by default) stops a node being reported against its own
ancestor, since a domain and a domain inside it are not competing for a
resource.

`unless-flag` names a flag that excuses a clash when *every* group involved
sets it -- how a deliberately shared device or memory region is allowed.
`ignore-nested` (on by default) stops a node being reported against its own
ancestor, since a domain and a domain inside it are not competing for a
resource.

## Collectors

`collect:` says which values to pull out of each node for a comparison.

- **`property:<name>`** -- each value of that property, compared for equality.
- **`{property: <name>, kind: range}`** -- `(start, size)` pairs from an
  address/size property, compared for overlap and containment. Cell widths
  follow the tree, or can be pinned with `address-cells` / `size-cells`.
- **`access`** -- the devices a domain is given, each with its flags decoded so
  `unless-flag` can be honoured.
- **`cpu-cores`** -- one entry per CPU core a domain claims, from the `cpus`
  triplets.

## Relations

`{relative: <relation>}` reaches a node from the one the enclosing context bound.

- **`parent-domain`** -- the domain named by this node's `parent` property.
- **`ancestor-domain`** -- the nearest domain this node sits inside in the tree.
- **`guests`** -- the domains nested directly inside this one.
  `children-domains` is the same thing, under a name that does not assume a
  hypervisor.
- **`cluster`** -- the CPU cluster(s) this domain's `cpus` reference.
- **any property name** -- whatever that reference-valued property names, for
  cases with no fixed relation.

`parent-domain` and `ancestor-domain` are different hierarchies and both are
real: a domain may name a `parent` property, and separately may be nested
inside another domain in the tree. Hypervisor and guest use the latter.

## Which tree a rule looks at

By default a rule runs against the **assembled system device tree** -- the
point where every input has been merged, and therefore the only place where
the semantics are complete. Checking one input against another before that
merge is not necessary: after it, they are the same tree.

A rule may name a different one:

```yaml
  - id: my-other-check
    tree: my-extracted-tree     # default: the assembled SDT
```

The name is looked up among the trees lopper is already holding alongside the
main one: those built by a `lop,tree` operation, and trees pulled out of, or
overlaid onto, the system device tree. It is the same set `lop,select` reaches
with its own `tree` property. Children of a context inherit it.

If the named tree is not loaded the rule is **skipped with a warning**, and
does not silently count as passing.

An external artifact is checked the same way: bring it in as a tree, then
write ordinary rules against it. Nothing in the language is specific to where
a tree came from.
