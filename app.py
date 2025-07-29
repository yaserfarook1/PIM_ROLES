import streamlit as st
import msal
import requests
from openai import AzureOpenAI
import json
import logging
import os
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

# Escape iframe (required on Streamlit Cloud)
st.markdown("""
    <script>
        if (window.top !== window.self) {
            window.top.location = window.location.href;
        }
    </script>
""", unsafe_allow_html=True)

# Logging config
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(), logging.FileHandler('pim_role_request.log')]
)
logger = logging.getLogger(__name__)

# Azure AD config
CLIENT_ID = os.getenv("CLIENT_ID")
TENANT_ID = os.getenv("TENANT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
REDIRECT_URI = "https://pimroles0.streamlit.app"  # <== NOTE: No `/login` in the redirect
SCOPES = ["User.Read", "Directory.Read.All", "Mail.Send"]

# OpenAI config
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_DEPLOYMENT = "gpt-4o"

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")

# Initialize clients
app = msal.ConfidentialClientApplication(
    CLIENT_ID,
    authority=AUTHORITY,
    client_credential=CLIENT_SECRET
)
openai_client = AzureOpenAI(
    api_key=AZURE_OPENAI_API_KEY,
    api_version="2024-04-01-preview",
    azure_endpoint=AZURE_OPENAI_ENDPOINT
)

st.title("🔐 PIM Role Access Request Portal")

# Authenticate flow
def authenticate_user():
    if "token" not in st.session_state:
        login_url = app.get_authorization_request_url(SCOPES, redirect_uri=REDIRECT_URI)
        st.markdown(f"""
        <a href="{login_url}">
            <button>Login with Microsoft</button>
        </a>
        """, unsafe_allow_html=True)
        return None
    return st.session_state["token"]

# Callback handling
def handle_callback():
    query_params = st.query_params
    if "code" in query_params:
        code = query_params["code"]
        logger.info(f"Received auth code: {code[:10]}...")
        try:
            result = app.acquire_token_by_authorization_code(code, SCOPES, redirect_uri=REDIRECT_URI)
            if "access_token" in result:
                st.session_state["token"] = result["access_token"]
                st.session_state["user"] = result["id_token_claims"]
                st.query_params.clear()  # Clear ?code param
                st.rerun()
            else:
                st.error(f"Auth failed: {result.get('error_description', 'Unknown error')}")
        except Exception as e:
            st.error(f"Token error: {e}")
    elif "error" in query_params:
        st.error(f"OAuth error: {query_params.get('error_description', 'Unknown')}")
    return st.session_state.get("token")

# Fetch PIM roles
def get_pim_roles(access_token):
    try:
        headers = {"Authorization": f"Bearer {access_token}"}
        response = requests.get("https://graph.microsoft.com/v1.0/roleManagement/directory/roleDefinitions", headers=headers)
        if response.status_code == 200:
            return response.json().get("value", [])
        else:
            st.error("Failed to fetch PIM roles")
            return []
    except Exception as e:
        st.error(f"Error fetching roles: {e}")
        return []

# Draft email with OpenAI
def draft_email(user_name, user_id, role_name):
    prompt = f"""
    Draft a professional email requesting access to a PIM role. Include the user's name, user principal ID, and the role name.

    User Name: {user_name}
    User Principal ID: {user_id}
    Role Name: {role_name}
    """
    try:
        response = openai_client.chat.completions.create(
            model=AZURE_OPENAI_DEPLOYMENT,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content
    except Exception as e:
        st.error(f"OpenAI error: {e}")
        return None

# Send email
def send_email(access_token, user_email, email_content):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    email_data = {
        "message": {
            "subject": email_content.split("\n")[0].replace("Subject: ", ""),
            "body": {
                "contentType": "Text",
                "content": email_content
            },
            "toRecipients": [{"emailAddress": {"address": ADMIN_EMAIL}}],
            "from": {"emailAddress": {"address": user_email}}
        },
        "saveToSentItems": "true"
    }
    try:
        response = requests.post("https://graph.microsoft.com/v1.0/me/sendMail", headers=headers, json=email_data)
        return response.status_code == 202
    except Exception as e:
        st.error(f"Email error: {e}")
        return False

# Entry logic
if "token" not in st.session_state:
    token = handle_callback() or authenticate_user()
else:
    token = st.session_state["token"]

# Main UI
if token:
    user = st.session_state["user"]
    user_name = user.get("name", "Unknown")
    user_id = user.get("oid", "Unknown")
    user_email = user.get("preferred_username", "Unknown")

    st.success(f"Welcome, {user_name}!")
    roles = get_pim_roles(token)
    role_names = [role["displayName"] for role in roles]
    selected_role = st.selectbox("Select a PIM Role", role_names)

    if st.button("Request Access"):
        if selected_role:
            email_content = draft_email(user_name, user_id, selected_role)
            if email_content:
                st.text_area("📧 Drafted Email", email_content, height=300)
                if send_email(token, user_email, email_content):
                    st.success("✅ Request email sent!")
                else:
                    st.error("❌ Failed to send email.")
        else:
            st.warning("Please select a role.")

