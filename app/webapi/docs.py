from __future__ import annotations

import html


def build_scalar_docs_html(*, title: str, openapi_url: str, theme: str = "modern") -> str:
    escaped_title = html.escape(title)
    escaped_openapi_url = html.escape(openapi_url)
    escaped_theme = html.escape(theme)
    return f"""<!DOCTYPE html>\n<html lang=\"en\">\n  <head>\n    <meta charset=\"utf-8\" />\n    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />\n    <title>{escaped_title} API Reference</title>\n    <script src=\"https://cdn.jsdelivr.net/npm/@scalar/api-reference\"></script>\n    <style>\n      html, body {{\n        height: 100%;\n      }}\n      body {{\n        margin: 0;\n      }}\n      scalar-api-reference {{\n        height: 100%;\n      }}\n    </style>\n  </head>\n  <body>\n    <script>\n      const reference = document.createElement('scalar-api-reference');\n      reference.configuration = {{\n        spec: {{\n          url: '{escaped_openapi_url}'\n        }},\n        theme: '{escaped_theme}'\n      }};\n      document.body.appendChild(reference);\n    </script>\n  </body>\n</html>\n"""
