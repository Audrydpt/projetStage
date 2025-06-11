import json
from typing import Annotated, Any, Dict, Optional
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse
from typing_extensions import Doc

from fastapi.openapi.docs import swagger_ui_default_parameters


def get_custom_swagger_ui_html(
    *,
    openapi_url: Annotated[
        str,
        Doc(
            """
            The OpenAPI URL that Swagger UI should load and use.

            This is normally done automatically by FastAPI using the default URL
            `/openapi.json`.
            """
        ),
    ],
    title: Annotated[
        str,
        Doc(
            """
            The HTML `<title>` content, normally shown in the browser tab.
            """
        ),
    ],
    swagger_js_url: Annotated[
        str,
        Doc(
            """
            The URL to use to load the Swagger UI JavaScript.

            It is normally set to a CDN URL.
            """
        ),
    ] = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.9.0/swagger-ui-bundle.js",
    swagger_css_url: Annotated[
        str,
        Doc(
            """
            The URL to use to load the Swagger UI CSS.

            It is normally set to a CDN URL.
            """
        ),
    ] = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.9.0/swagger-ui.css",
    swagger_favicon_url: Annotated[
        str,
        Doc(
            """
            The URL of the favicon to use. It is normally shown in the browser tab.
            """
        ),
    ] = "https://fastapi.tiangolo.com/img/favicon.png",
    oauth2_redirect_url: Annotated[
        Optional[str],
        Doc(
            """
            The OAuth2 redirect URL, it is normally automatically handled by FastAPI.
            """
        ),
    ] = None,
    init_oauth: Annotated[
        Optional[Dict[str, Any]],
        Doc(
            """
            A dictionary with Swagger UI OAuth2 initialization configurations.
            """
        ),
    ] = None,
    swagger_ui_parameters: Annotated[
        Optional[Dict[str, Any]],
        Doc(
            """
            Configuration parameters for Swagger UI.

            It defaults to [swagger_ui_default_parameters][fastapi.openapi.docs.swagger_ui_default_parameters].
            """
        ),
    ] = None,
) -> HTMLResponse:
    """
    Generate and return the HTML  that loads Swagger UI for the interactive
    API docs (normally served at `/docs`).

    You would only call this function yourself if you needed to override some parts,
    for example the URLs to use to load Swagger UI's JavaScript and CSS.

    Read more about it in the
    [FastAPI docs for Configure Swagger UI](https://fastapi.tiangolo.com/how-to/configure-swagger-ui/)
    and the [FastAPI docs for Custom Docs UI Static Assets (Self-Hosting)](https://fastapi.tiangolo.com/how-to/custom-docs-ui-assets/).
    """
    current_swagger_ui_parameters = swagger_ui_default_parameters.copy()
    if swagger_ui_parameters:
        current_swagger_ui_parameters.update(swagger_ui_parameters)

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <link type="text/css" rel="stylesheet" href="{swagger_css_url}">
    <link rel="shortcut icon" href="{swagger_favicon_url}">
    <title>{title}</title>
    </head>
    <body>
    <div id="swagger-ui">
    </div>
    <script src="{swagger_js_url}"></script>
    <!-- `SwaggerUIBundle` is now available on the page -->
    <style>
        input[type=datetime-local], .swagger-ui input[type=date] {{
            background: #fff;
            border: 1px solid #d9d9d9;
            border-radius: 4px;
            margin: 5px 0;
            min-width: 100px;
            padding: 8px 10px;
            line-height:1;
            box-sizing: border-box;
        }}
    </style>
    
    <script src="https://unpkg.com/react@18/umd/react.development.js"></script>
    <script src="https://unpkg.com/react-dom@18/umd/react-dom.development.js"></script>

    <script>
        class JsonSchema_string_date extends React.Component {{
            constructor(props) {{
                super(props);
                this.state = {{
                    date: currentDateTime().substring(0,10)
                }};
            }}
            handleDateChange = (event) => {{
                const selectedDate = event.target.value;
                this.setState({{ date: selectedDate }});
                this.props.onChange(selectedDate);
            }};
            render() {{
                const input = React.createElement("input", {{ type: "date", value: null, onChange: this.handleDateChange}});
                return input;
            }}
        }}
        const currentDateTime = () => {{
            var tzoffset = new Date().getTimezoneOffset() * 60000; //offset in milliseconds
            var localISOString = new Date(Date.now() - tzoffset)
                .toISOString()
                .slice(0, -1);

            // convert to YYYY-MM-DDTHH:MM
            const datetimeInputString = localISOString.substring(
                0,
                ((localISOString.indexOf("T") | 0) + 6) | 0
            );
            console.log(datetimeInputString);
            return datetimeInputString;
        }}
        class JsonSchema_string_date_time extends React.Component {{
            constructor(props) {{
                super(props);
                this.state = {{
                    datetime: currentDateTime()
                }};
            }}
            handleDateTimeChange = (event) => {{
                const selectedDatetime = event.target.value;
                this.setState({{ datetime: selectedDatetime }});
                this.props.onChange(selectedDatetime);
            }};
            render() {{
                const input = React.createElement("input", {{ type: "datetime-local", value: null, onChange: this.handleDateTimeChange}});
                return input;
            }}
        }}
        const DateTimeSwaggerPlugin = function(system) {{
            return {{
                components: {{
                    JsonSchema_string_date: JsonSchema_string_date,
                    "JsonSchema_string_date-time": JsonSchema_string_date_time
                }}            
            }}
        }}
    </script>
    <script>
    const ui = SwaggerUIBundle({{
        url: '{openapi_url}',
    """

    for key, value in current_swagger_ui_parameters.items():
        html += f"{json.dumps(key)}: {json.dumps(jsonable_encoder(value))},\n"

    if oauth2_redirect_url:
        html += f"oauth2RedirectUrl: window.location.origin + '{oauth2_redirect_url}',"

    html += """
    presets: [
        SwaggerUIBundle.presets.apis,
        SwaggerUIBundle.SwaggerUIStandalonePreset
        ],
    plugins: [DateTimeSwaggerPlugin]
    })"""

    if init_oauth:
        html += f"""
        ui.initOAuth({json.dumps(jsonable_encoder(init_oauth))})
        """

    html += """
    </script>
    </body>
    </html>
    """
    return HTMLResponse(html)