"""Deterministic recommendation-paper network derived from archive evidence.

The original graph keeps directed citations and experimental comparisons. This
website projection retains all of that evidence, but draws one composite link
per paper pair and uses the complete technical-tag vocabulary for its layout.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from hashlib import sha256
import json
import math
import re


_RECOMMENDATION_CATEGORIES = {"generative-rec", "discriminative-rec"}
_NON_TECHNICAL_TAGS = {"industrial", "academic"}
_VERSION_SUFFIX = re.compile(r"(?:[-_\s]v)\d+(?:\.\d+)*$", re.IGNORECASE)


def _values(value) -> list:
    return [value] if isinstance(value, str) else list(value or [])


def _day(value) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _pair_id(source: str, target: str) -> str:
    encoded = json.dumps([source, target], ensure_ascii=False, separators=(",", ":"))
    return "link:" + sha256(encoded.encode()).hexdigest()[:20]


def _version_names(node: dict) -> list[tuple[str, bool]]:
    result = []
    for name in [node.get("model_name", ""), *_values(node.get("aliases"))]:
        value = str(name).strip().casefold()
        match = _VERSION_SUFFIX.search(value)
        result.append((value[:match.start()].rstrip() if match else value, bool(match)))
    return result


def _timeline(nodes: list[dict]) -> list[float]:
    """Spread publication dates using 90% date rank and 10% calendar distance.

    Equal dates share one coordinate; distinct dates remain strictly ordered.
    Ranking distinct dates gives a busy recent period enough room without
    letting a few historical baselines consume most of the map. Undated
    papers stay in the left margin rather than impersonating recent papers.
    """
    dates = [_day(node.get("published_date")) for node in nodes]
    known = sorted({day for day in dates if day is not None})
    if not known:
        return [0.02] * len(nodes)
    oldest, newest = known[0], known[-1]
    if oldest == newest:
        return [0.96 if day else 0.02 for day in dates]
    span = (newest - oldest).days
    positions = {
        day: 0.04 + 0.92 * (0.9 * rank / (len(known) - 1) + 0.1 * (day - oldest).days / span)
        for rank, day in enumerate(known)
    }
    return [positions[day] if day else 0.02 for day in dates]


def _spectral_seed(ids: list[str], pairs: list[tuple[int, int, float]]) -> list[float]:
    """Two continuous affinity coordinates, using sparse power iteration.

    Orthogonalizing against the constant normalized mode prevents a popular
    baseline from becoming a layout centre. There are no group assignments,
    theme anchors, or manually positioned model families.
    """
    count = len(ids)
    strength = [0.0] * count
    for i, j, weight in pairs:
        strength[i] += weight
        strength[j] += weight
    roots = [math.sqrt(max(value, 1e-12)) for value in strength]
    norm = math.sqrt(sum(value * value for value in roots)) or 1
    constant = [value / norm for value in roots]
    normalized = [(i, j, weight / (roots[i] * roots[j])) for i, j, weight in pairs]

    def orthogonalize(vector: list[float], basis: list[list[float]]) -> list[float]:
        for axis in basis:
            projection = sum(a * b for a, b in zip(vector, axis))
            vector = [value - projection * axis[i] for i, value in enumerate(vector)]
        length = math.sqrt(sum(value * value for value in vector))
        return [value / length for value in vector] if length > 1e-12 else [0.0] * count

    basis = [constant]
    for component in range(min(2, max(0, count - 1))):
        vector = [
            int(sha256(f"{component}:{node_id}".encode()).hexdigest()[:8], 16) / 2**31 - 1
            for node_id in ids
        ]
        vector = orthogonalize(vector, basis)
        # (I + normalized affinity) / 2 retains positive ordering while
        # suppressing alternating negative-eigenvalue modes.
        for _ in range(160):
            updated = [0.5 * value for value in vector]
            for i, j, weight in normalized:
                updated[i] += 0.5 * weight * vector[j]
                updated[j] += 0.5 * weight * vector[i]
            updated = orthogonalize(updated, basis)
            delta = sum((a - b) ** 2 for a, b in zip(updated, vector))
            vector = updated
            if delta < 1e-11:
                break
        if vector and vector[max(range(count), key=lambda i: abs(vector[i]))] < 0:
            vector = [-value for value in vector]
        basis.append(vector)
    if len(basis) == 1:
        return [0.5] * count
    raw = [0.82 * basis[1][i] + (0.18 * basis[2][i] if len(basis) > 2 else 0) for i in range(count)]
    ranked = sorted(raw)
    low, high = ranked[int((count - 1) * 0.03)], ranked[int((count - 1) * 0.97)]
    span = high - low
    if span < 1e-12:
        return [0.5] * count
    return [min(1.0, max(0.0, (value - low) / span)) for value in raw]


def _vertical_layout(nodes: list[dict], links: list[dict], layout_ids: set[str]) -> list[float]:
    """Relax the sparse affinity network vertically while holding time fixed."""
    if len(nodes) < 2:
        return [0.5] * len(nodes)
    index = {node["id"]: i for i, node in enumerate(nodes)}
    pairs = [(index[link["source"]], index[link["target"]], link["weight"])
             for link in links if link["id"] in layout_ids]
    seed = _spectral_seed([node["id"] for node in nodes], pairs)
    initial = [32 + value * 406 for value in seed]
    positions = initial[:]
    velocity = [0.0] * len(nodes)
    x = [node["x"] * 950 for node in nodes]
    # Only neighbours within the force radius can repel; date coordinates are
    # fixed, so build this list once rather than testing every pair per step.
    nearby = [(i, j, (x[i] - x[j]) ** 2) for i in range(len(nodes)) for j in range(i)
              if abs(x[i] - x[j]) < 230]
    for step in range(480):
        force = [0.005 * (initial[i] - value) + (
            0.07 * (22 - value) if value < 22 else
            0.07 * (448 - value) if value > 448 else 0
        ) for i, value in enumerate(positions)]
        for i, j, weight in pairs:
            pull = 0.082 * weight * (positions[j] - positions[i])
            force[i] += pull
            force[j] -= pull
        for i, j, dx_squared in nearby:
            dy = positions[i] - positions[j]
            # Identical seeds (e.g. isolated papers) need a deterministic nudge.
            if abs(dy) < 1e-8:
                dy = 0.01 if (i + j) % 2 else -0.01
            distance_squared = max(49, dx_squared + dy * dy)
            if distance_squared > 230 * 230:
                continue
            repel = 1900 * dy / (distance_squared * math.sqrt(distance_squared))
            force[i] += repel
            force[j] -= repel
        maximum_motion = 0.0
        for i in range(len(nodes)):
            velocity[i] = max(-2.3, min(2.3, 0.7 * velocity[i] + force[i] * 0.55))
            positions[i] += velocity[i]
            maximum_motion = max(maximum_motion, abs(velocity[i]))
        if step > 240 and maximum_motion < 0.002:
            break
    # Soft walls avoid pinning many papers onto identical top/bottom rows.
    low, high = min(22, min(positions)), max(448, max(positions))
    return [round(0.04 + 0.92 * (value - low) / (high - low), 8) for value in positions]


def _select_links(links: list[dict], ids: list[str]) -> tuple[set[str], set[str]]:
    """Keep top-five layout affinity, drawing only 1–3 strong local contacts."""
    by_node = {node_id: [] for node_id in ids}
    for link in links:
        by_node[link["source"]].append(link)
        by_node[link["target"]].append(link)
    layout_ids = {link["id"] for neighbours in by_node.values() for link in neighbours[:5]}
    layout_ids.update(link["id"] for link in links if link["series"])
    strongest = {node_id: {link["id"] for link in neighbours[:2]} for node_id, neighbours in by_node.items()}
    visible = set()
    degree = Counter()

    def add(link: dict) -> None:
        if link["id"] not in visible:
            visible.add(link["id"])
            degree[link["source"]] += 1
            degree[link["target"]] += 1

    def available(link: dict) -> bool:
        return degree[link["source"]] < 3 and degree[link["target"]] < 3

    for link in links:
        if link["series"] and available(link):
            add(link)
    for link in links:
        if (link["id"] in strongest[link["source"]] and
                link["id"] in strongest[link["target"]] and available(link)):
            add(link)
    for node_id in sorted(ids, key=lambda key: (by_node[key][0]["weight"] if by_node[key] else 0, key)):
        if degree[node_id]:
            continue
        for link in by_node[node_id]:
            if available(link):
                add(link)
                break
    for node_id in sorted(ids, key=lambda key: (degree[key], key)):
        if degree[node_id] >= 2:
            continue
        for link in by_node[node_id][:5]:
            if link["id"] not in visible and available(link):
                add(link)
                break

    parent = {node_id: node_id for node_id in ids}

    def find(node_id: str) -> str:
        while parent[node_id] != node_id:
            parent[node_id] = parent[parent[node_id]]
            node_id = parent[node_id]
        return node_id

    for link in links:
        if link["id"] in visible:
            parent[find(link["source"])] = find(link["target"])
    bridges = 0
    for link in links:
        if bridges >= 5:
            break
        left, right = find(link["source"]), find(link["target"])
        if left != right and available(link):
            add(link)
            parent[left] = right
            bridges += 1
    return layout_ids | visible, visible


def prepare_recommendation_network(explorer: dict) -> dict:
    """Build a read-only, complete recommendation-domain network for the UI."""
    all_nodes = explorer.get("nodes", [])
    nodes = [dict(node) for node in all_nodes if node.get("has_reading") and
             _RECOMMENDATION_CATEGORIES.intersection(_values(node.get("category")))]
    nodes.sort(key=lambda node: node["id"])
    by_id = {node["id"]: node for node in nodes}
    ids = list(by_id)
    as_of = explorer.get("as_of", "")
    for node, x in zip(nodes, _timeline(nodes)):
        node["x"] = round(x, 8)

    relations = [dict(relation) for relation in explorer.get("relations", [])
                 if relation.get("source") in by_id and relation.get("target") in by_id]
    relations.sort(key=lambda relation: relation["id"])
    real_by_pair = defaultdict(list)
    relation_degree = Counter()
    for relation in relations:
        if relation["source"] == relation["target"]:
            continue
        pair = tuple(sorted((relation["source"], relation["target"])))
        real_by_pair[pair].append(relation)
        relation_degree.update(pair)

    tags = {node_id: set(_values(node.get("tags"))) for node_id, node in by_id.items()}
    technical = {node_id: values - _NON_TECHNICAL_TAGS for node_id, values in tags.items()}
    tag_counts = Counter(tag for values in tags.values() for tag in values)
    idf_squared = {tag: math.log((len(nodes) + 1) / (count + 1)) ** 2
                   for tag, count in tag_counts.items() if tag not in _NON_TECHNICAL_TAGS}
    norms = {node_id: math.sqrt(sum(idf_squared[tag] for tag in values)) for node_id, values in technical.items()}
    version_names = {node_id: _version_names(node) for node_id, node in by_id.items()}
    links = []
    for i, source in enumerate(ids):
        for target in ids[i + 1:]:
            evidence = real_by_pair.get((source, target), [])
            shared = technical[source] & technical[target]
            information = sum(idf_squared[tag] for tag in shared)
            cosine = information / max(norms[source] * norms[target], 1e-12)
            similarity = cosine * (1 - math.exp(-information / 1.5))
            direct = sum(1 if relation["type"] == "citation" else 0.22 for relation in evidence)
            direct /= math.sqrt(max(1, relation_degree[source]) * max(1, relation_degree[target]))
            relation_strength = 1 - math.exp(-4 * direct)
            series = bool(evidence) and any(
                left == right and len(left) >= 4 and (left_version or right_version)
                for left, left_version in version_names[source]
                for right, right_version in version_names[target]
            )
            weight = min(1.0, 0.64 * similarity + 0.34 * relation_strength + (0.12 if series else 0))
            if weight <= 0.004 and not evidence:
                continue
            links.append({
                "id": _pair_id(source, target),
                "source": source,
                "target": target,
                "shared_tags": sorted(tags[source] & tags[target], key=lambda tag: (-idf_squared.get(tag, -1), tag)),
                "relation_ids": [relation["id"] for relation in evidence],
                "types": sorted({relation["type"] for relation in evidence}),
                "series": series,
                "weight": round(weight, 6),
                "display": False,
            })
    links.sort(key=lambda link: (-link["weight"], link["id"]))
    layout_ids, visible_ids = _select_links(links, ids)
    # Keep every real relationship even when it is not among top-five layout
    # contacts. Selection can reveal that evidence without reloading the graph.
    links = [link for link in links if link["id"] in layout_ids or link["relation_ids"]]
    for link in links:
        link["display"] = link["id"] in visible_ids
    for node, y in zip(nodes, _vertical_layout(nodes, links, layout_ids)):
        node["y"] = y

    return {
        "version": 1,
        "as_of": as_of,
        "nodes": nodes,
        "relations": relations,
        "links": links,
        "topics": [{"id": tag, "label": tag, "count": count} for tag, count in sorted(tag_counts.items())],
        "stats": {
            "read_papers": len(nodes),
            "links": len(links),
            "visible_links": len(visible_ids),
            "relations": len(relations),
            "topics": len(tag_counts),
            "layout_links": len(layout_ids),
            "excluded_read_papers": sum(bool(node.get("has_reading")) for node in all_nodes) - len(nodes),
            "undated_papers": sum(_day(node.get("published_date")) is None for node in nodes),
        },
    }
