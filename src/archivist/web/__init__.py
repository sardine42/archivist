"""Archivist web application."""

from hashlib import sha256
from pathlib import Path

from flask import Flask


def static_asset_version(filename: str) -> str:
    """Change an asset's cache key only when its own content changes."""
    asset_path = Path(__file__).parent / "static" / filename
    return sha256(asset_path.read_bytes()).hexdigest()[:12]


def create_app(mode: str = "dev") -> Flask:
    """Create the Flask application.

    Args:
        mode: 'dev' for local development (all features),
              'search' for production server (search API only).
    """
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )
    app.config["MODE"] = mode
    app.config["SECRET_KEY"] = "dev-secret-key"  # Override from config.yaml in production

    from archivist.web.titles import format_paper_title
    app.jinja_env.filters["paper_title"] = format_paper_title

    # Share content-based asset versions with the static builder.
    @app.context_processor
    def inject_globals():
        return {
            "mode": mode,
            "css_version": static_asset_version("style.css"),
            "article_content_css_version": static_asset_version("article-content.css"),
            "article_content_js_version": static_asset_version("article-content.js"),
            "mailbox_css_version": static_asset_version("mailbox.css"),
            "mailbox_js_version": static_asset_version("mailbox.js"),
            "admin_auth_css_version": static_asset_version("admin-auth.css"),
            "admin_auth_js_version": static_asset_version("admin-auth.js"),
            "paper_search_version": static_asset_version("paper-search.js"),
            "reading_list_css_version": static_asset_version("reading-list.css"),
            "reading_detail_css_version": static_asset_version("reading-detail.css"),
            "reading_detail_js_version": static_asset_version("reading-detail.js"),
            "graph_layout_js_version": static_asset_version("graph-layout.js"),
            "graph_model_js_version": static_asset_version("graph-model.js"),
            "graph_map_js_version": static_asset_version("graph-map.js"),
            "graph_map_css_version": static_asset_version("graph-map.css"),
            "graph_explorer_js_version": static_asset_version("graph-explorer.js"),
            "graph_explorer_css_version": static_asset_version("graph-explorer.css"),
            "recommendation_map_js_version": static_asset_version("recommendation-map.js"),
            "recommendation_map_css_version": static_asset_version("recommendation-map.css"),
        }

    # Register blueprints
    from archivist.web.routes.reading import reading_bp
    app.register_blueprint(reading_bp)
    from archivist.web.routes.site import site_bp
    app.register_blueprint(site_bp)
    from archivist.web.mailbox import register_mailbox
    register_mailbox(app)
    from archivist.web.auth import register_auth
    register_auth(app)

    # Root redirect
    @app.route("/")
    def index():
        from flask import redirect
        return redirect("/reading/")

    return app
