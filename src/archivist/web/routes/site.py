"""Graph / Benchmark / Docs routes for the dev preview server.

These mirror the pages produced by ``archivist build`` so ``archivist web``
serves the full site, not just the reading section. The graph/benchmark pages
fetch their payload from sibling ``data.json`` endpoints, and their shared
model index is served at ``/search/model-index.json``. Paper full-text search
uses the same Pagefind bundle as the static site at ``/search/pagefind/``.
"""

import json
import mimetypes

from flask import Blueprint, Response, abort, render_template, send_file

from archivist.config import DOCS_DIR
from archivist.web.data import (
    load_docs,
    prepare_benchmark_data,
    prepare_graph_data,
    prepare_model_index,
)
from archivist.web.routes.reading import _render_markdown

site_bp = Blueprint("site", __name__)


def _json(payload) -> Response:
    return Response(json.dumps(payload, ensure_ascii=False), mimetype="application/json")


# ── Model graph ──────────────────────────────────────────────

@site_bp.route("/graph/")
def graph():
    return render_template("graph.html")


@site_bp.route("/graph/data.json")
def graph_data():
    return _json(prepare_graph_data())


# ── Benchmarks ───────────────────────────────────────────────

@site_bp.route("/benchmark/")
def benchmark():
    return render_template("benchmark.html")


@site_bp.route("/benchmark/data.json")
def benchmark_data():
    return _json(prepare_benchmark_data())


# ── Shared model index and paper full-text search ──────────────

@site_bp.route("/search/model-index.json")
def model_index():
    return _json(prepare_model_index())


@site_bp.route("/search/pagefind/<path:filename>")
def paper_search_asset(filename: str):
    """Serve the same static paper index in the local preview server."""
    from archivist.web.search import preview_search_asset

    content = preview_search_asset(filename)
    if content is None:
        abort(404)
    mimetype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    response = Response(content, mimetype=mimetype)
    response.cache_control.no_cache = True
    return response


# ── Internal docs ────────────────────────────────────────────

@site_bp.route("/docs/")
def docs_list():
    return render_template("docs/index.html", docs=load_docs(DOCS_DIR))


@site_bp.route("/docs/<slug>/")
def doc_detail(slug: str):
    doc = next((d for d in load_docs(DOCS_DIR) if d["slug"] == slug), None)
    if not doc:
        abort(404)
    content = _render_markdown(doc["body"])
    toc = getattr(_render_markdown, "_last_toc", "")
    return render_template("docs/detail.html", doc=doc, content=content, toc=toc)


@site_bp.route("/docs/<slug>/figures/<path:filename>")
def doc_figure(slug: str, filename: str):
    fig = DOCS_DIR / slug / "figures" / filename
    if not fig.exists():
        abort(404)
    return send_file(fig)
