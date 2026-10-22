"""Synthetic, read-only checks for the public recommendation-map projection."""

from copy import deepcopy
import unittest

from archivist.web.graph_explorer import prepare_graph_explorer
from archivist.web.graph_network import prepare_recommendation_network


def paper(node_id, *, category=None, tags=None, published="2026-01-01", read=True, model=None):
    return {
        "id": node_id,
        "model_name": model or node_id,
        "aliases": [],
        "title": f"Paper {node_id}",
        "category": ["generative-rec"] if category is None else category,
        "tags": [] if tags is None else tags,
        "published_date": published,
        "has_reading": read,
        "reading_url": f"/reading/{node_id}/" if read else "",
    }


def relation(relation_id, source, target, kind="citation", **extra):
    return {
        "id": relation_id, "source": source, "target": target,
        "type": kind, "summary": f"Evidence {relation_id}",
        **extra,
    }


def project(nodes, relations=()):
    return prepare_recommendation_network({
        "nodes": nodes, "relations": list(relations), "as_of": "2026-10-21",
    })


class RecommendationNetworkTests(unittest.TestCase):
    def test_scope_requires_reading_and_recommendation_category(self):
        result = project([
            paper("generative"),
            paper("discriminative", category="discriminative-rec"),
            paper("mixed", category=["generative-rec", "llm"]),
            paper("llm", category=["llm"], tags=["recommendation", "industrial"]),
            paper("other", category=["other"]),
            paper("unread", read=False),
        ])

        self.assertEqual({node["id"] for node in result["nodes"]}, {
            "generative", "discriminative", "mixed",
        })
        self.assertEqual(result["stats"]["read_papers"], 3)
        self.assertEqual(result["stats"]["excluded_read_papers"], 2)

    def test_complete_tags_and_original_fields_are_retained_without_mutation(self):
        tags = ["industrial", "semantic-id", "contrastive-learning", "long-sequence", "rl", "retrieval"]
        node = paper("a", tags=tags)
        node["custom_metadata"] = {"preserved": True}
        source = {"nodes": [node], "relations": [], "as_of": "2026-10-21"}
        before = deepcopy(source)

        result = prepare_recommendation_network(source)

        self.assertEqual(source, before)
        self.assertEqual(result["nodes"][0]["tags"], tags)
        self.assertEqual(result["nodes"][0]["custom_metadata"], {"preserved": True})
        self.assertEqual({topic["id"] for topic in result["topics"]}, set(tags))
        self.assertTrue(all(topic["count"] == 1 for topic in result["topics"]))

    def test_provenance_alone_does_not_invent_affinity(self):
        result = project([
            paper("a", tags=["industrial", "academic"]),
            paper("b", tags=["industrial", "academic"]),
            paper("c", tags=["industrial"]),
        ])

        self.assertEqual(result["links"], [])
        self.assertEqual({topic["id"] for topic in result["topics"]}, {"industrial", "academic"})

    def test_technical_tags_after_the_first_two_still_contribute_affinity(self):
        result = project([
            paper("a", tags=["industrial", "academic", "semantic-id"]),
            paper("b", tags=["industrial", "academic", "semantic-id"]),
            paper("c", tags=["industrial", "academic", "long-sequence"]),
        ])

        link = next(link for link in result["links"] if {link["source"], link["target"]} == {"a", "b"})
        self.assertIn("semantic-id", link["shared_tags"])
        self.assertGreater(link["weight"], 0)

    def test_undated_papers_stay_before_known_dates_and_dates_are_monotonic(self):
        result = project([
            paper("latest", published="2026-10-20"),
            paper("middle", published="2024-05-01"),
            paper("old", published="2019-01-01"),
            paper("missing", published=""),
            paper("invalid", published="not-a-date"),
        ])
        nodes = {node["id"]: node for node in result["nodes"]}

        self.assertLess(nodes["missing"]["x"], nodes["old"]["x"])
        self.assertLess(nodes["invalid"]["x"], nodes["old"]["x"])
        self.assertLess(nodes["old"]["x"], nodes["middle"]["x"])
        self.assertLess(nodes["middle"]["x"], nodes["latest"]["x"])
        self.assertEqual(result["stats"]["undated_papers"], 2)

    def test_one_pair_link_preserves_all_directions_and_evidence_types(self):
        evidence = [
            relation("a-cites-b", "a", "b"),
            relation("b-cites-a", "b", "a"),
            relation("compare", "b", "a", "comparison", datasets={"benchmark": "a reported result"}),
        ]
        result = project([paper("a"), paper("b")], evidence)

        self.assertEqual(len(result["links"]), 1)
        link = result["links"][0]
        self.assertEqual({link["source"], link["target"]}, {"a", "b"})
        self.assertEqual(set(link["relation_ids"]), {item["id"] for item in evidence})
        self.assertEqual(set(link["types"]), {"citation", "comparison"})
        self.assertEqual({item["id"]: item for item in result["relations"]}, {
            item["id"]: item for item in evidence
        })

    def test_dense_evidence_is_not_lost_when_default_links_are_sparse(self):
        nodes = [paper(f"n{i}") for i in range(9)]
        evidence = [
            relation(f"r{i}-{j}", left["id"], right["id"])
            for i, left in enumerate(nodes) for j, right in enumerate(nodes) if i < j
        ]
        result = project(nodes, evidence)

        self.assertEqual(len(result["links"]), len(evidence))
        self.assertLess(result["stats"]["visible_links"], len(evidence))
        self.assertEqual({key for link in result["links"] for key in link["relation_ids"]}, {
            item["id"] for item in evidence
        })
        self.assertEqual(len({frozenset((link["source"], link["target"])) for link in result["links"]}), len(evidence))

    def test_relations_to_papers_outside_scope_are_excluded(self):
        result = project([
            paper("a"), paper("b"), paper("llm", category=["llm"]), paper("unread", read=False),
        ], [
            relation("valid", "a", "b"), relation("llm", "a", "llm"),
            relation("unread", "a", "unread"), relation("missing", "a", "missing"),
        ])

        self.assertEqual([item["id"] for item in result["relations"]], ["valid"])
        self.assertEqual(result["links"][0]["relation_ids"], ["valid"])

    def test_citations_count_distinct_in_scope_sources_and_preserve_evidence(self):
        evidence = [
            relation("valid-1", "a", "b"),
            relation("duplicate-report", "a", "b"),
            relation("valid-2", "c", "b"),
            relation("opposite-direction", "b", "a"),
            relation("self", "b", "b"),
            relation("comparison", "d", "b", "comparison"),
            relation("outside-llm", "llm", "b"),
            relation("outside-unread", "unread", "b"),
            relation("outside-missing", "missing", "b"),
        ]
        result = project([
            paper("a"), paper("b"), paper("c"), paper("d"),
            paper("llm", category=["llm"]), paper("unread", read=False),
        ], evidence)
        nodes = {node["id"]: node for node in result["nodes"]}

        self.assertEqual(nodes["b"]["citation_count"], 2)
        self.assertEqual(nodes["b"]["citing_paper_ids"], ["a", "c"])
        self.assertEqual(nodes["a"]["citation_count"], 1)
        self.assertEqual(nodes["a"]["citing_paper_ids"], ["b"])
        self.assertEqual(nodes["c"]["citation_count"], 0)
        self.assertEqual(nodes["d"]["citing_paper_ids"], [])
        self.assertEqual(result["stats"]["max_citation_count"], 2)
        self.assertEqual({item["id"] for item in result["relations"]}, {
            "valid-1", "duplicate-report", "valid-2", "opposite-direction", "self", "comparison",
        })

    def test_citing_papers_are_newest_first_with_stable_ties_and_missing_dates_last(self):
        nodes = [
            paper("target"),
            paper("older", published="2023-01-01"),
            paper("new-b", published="2026-10-21"),
            paper("new-a", published="2026-10-21"),
            paper("unknown", published=""),
            paper("invalid", published="not-a-date"),
        ]
        result = project(nodes, [relation(node["id"], node["id"], "target") for node in nodes[1:]])
        target = next(node for node in result["nodes"] if node["id"] == "target")

        self.assertEqual(target["citation_count"], 5)
        self.assertEqual(target["citing_paper_ids"], ["new-a", "new-b", "older", "invalid", "unknown"])

    def test_citation_thresholds_use_global_nearest_rank_quantiles(self):
        nodes = [paper(f"n{i:03d}") for i in range(101)]
        # The sorted counts end in 2, 4, 6, 8, 10, 12. Nearest-rank
        # quantiles select index 95 for p95 and index 99 for p99.
        evidence = [
            relation(f"r{target}-{source}", f"n{source:03d}", f"n{target:03d}")
            for target, count in zip(range(95, 101), [2, 4, 6, 8, 10, 12])
            for source in range(count)
        ]
        result = project(nodes, evidence)

        self.assertEqual(result["stats"]["max_citation_count"], 12)
        self.assertEqual(result["stats"]["citation_emphasis_threshold"], 2)
        self.assertEqual(result["stats"]["citation_major_threshold"], 10)
        self.assertEqual(result["version"], 1)

    def test_uncited_and_empty_networks_do_not_emphasize_zero_counts(self):
        for nodes in ([], [paper("a")], [paper("a"), paper("b")]):
            with self.subTest(paper_count=len(nodes)):
                result = project(nodes)
                self.assertEqual(result["stats"]["max_citation_count"], 0)
                self.assertEqual(result["stats"]["citation_emphasis_threshold"], 1)
                self.assertEqual(result["stats"]["citation_major_threshold"], 1)
                self.assertTrue(all(node["citation_count"] == 0 and node["citing_paper_ids"] == []
                                    for node in result["nodes"]))

    def test_output_is_deterministic_when_input_order_changes(self):
        nodes = [
            paper("a", published="2025-03-01", tags=["semantic-id", "rl"]),
            paper("b", published="2026-02-01", tags=["semantic-id"]),
            paper("c", tags=["rl", "long-sequence"]),
            paper("d", tags=["transformer"]),
        ]
        evidence = [relation("r1", "a", "b"), relation("r2", "c", "a", "comparison")]

        result = project(nodes, evidence)
        reordered = project(list(reversed(nodes)), list(reversed(evidence)))

        self.assertEqual(result, reordered)
        for node in result["nodes"]:
            self.assertGreaterEqual(node["x"], 0)
            self.assertLessEqual(node["x"], 1)
            self.assertGreaterEqual(node["y"], 0)
            self.assertLessEqual(node["y"], 1)

    def test_generic_version_hint_requires_real_evidence(self):
        nodes = [
            paper("a", model="NovelMethod", tags=["semantic-id"]),
            paper("b", model="NovelMethod-V2", tags=["semantic-id"]),
            paper("c", model="OtherMethod", tags=["long-sequence"]),
        ]
        without_evidence = project(nodes)
        candidate = next(link for link in without_evidence["links"] if {link["source"], link["target"]} == {"a", "b"})

        self.assertFalse(candidate["series"])
        self.assertEqual(candidate["relation_ids"], [])
        self.assertEqual(without_evidence["relations"], [])

        with_evidence = project(nodes, [relation("citation", "b", "a")])
        supported = next(link for link in with_evidence["links"] if {link["source"], link["target"]} == {"a", "b"})
        self.assertTrue(supported["series"])
        self.assertEqual(supported["relation_ids"], ["citation"])

    def test_empty_and_singleton_archives_produce_valid_maps(self):
        empty = project([])
        self.assertEqual(empty["nodes"], [])
        self.assertEqual(empty["links"], [])
        self.assertEqual(empty["stats"]["read_papers"], 0)

        singleton = project([paper("only", published="")])
        self.assertEqual(len(singleton["nodes"]), 1)
        self.assertEqual(singleton["links"], [])
        self.assertGreaterEqual(singleton["nodes"][0]["y"], 0)
        self.assertLessEqual(singleton["nodes"][0]["y"], 1)

    def test_explorer_adds_network_without_removing_legacy_projection(self):
        graph = {
            "nodes": {
                "MixedMethod": {"paper_id": "2601.00001v2"},
                "LanguageMethod": {"paper_id": "2601.00002"},
            },
            "citations": [{"source": "MixedMethod", "target": "LanguageMethod"}],
        }
        explorer = prepare_graph_explorer(graph, {
            "2601.00001": {"title": "Recommendation paper", "model_name": "MixedMethod", "category": ["generative-rec", "llm"], "tags": ["semantic-id"]},
            "2601.00002": {"title": "Language paper", "category": "llm"},
        }, {
            "2601.00001": "/reading/mixed/", "2601.00002": "/reading/language/",
        }, lambda names: ", ".join(names), as_of="2026-10-21")

        self.assertEqual(len(explorer["nodes"]), 2)
        self.assertEqual(len(explorer["relations"]), 1)
        self.assertIn("lineages", explorer)
        self.assertEqual([node["id"] for node in explorer["network"]["nodes"]], ["paper:2601.00001"])
        self.assertEqual(explorer["network"]["relations"], [])
        self.assertEqual(explorer["network"]["as_of"], "2026-10-21")


if __name__ == "__main__":
    unittest.main()
