import streamlit as st
import msal
import requests
from azure.identity import DefaultAzureCredential
from openai import AzureOpenAI
import json
import logging
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('pim_role_request.log')
    ]
)
logger = logging.getLogger(__name__)

# Azure AD Configuration
CLIENT_ID = os.getenv("CLIENT_ID")
TENANT_ID = os.getenv("TENANT_ID")
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
REDIRECT_URI = "https://pimroles0.streamlit.app/"
SCOPES = ["User.Read", "Directory.Read.All", "Mail.Send"]

# Azure OpenAI Configuration
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_DEPLOYMENT = "gpt-4o"

# Admin Email
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")

# Initialize MSAL
app = msal.ConfidentialClientApplication(
    CLIENT_ID,
    authority=AUTHORITY,
    client_credential=os.getenv("CLIENT_SECRET")  # From .env file
)

# Initialize Azure OpenAI Client
openai_client = AzureOpenAI(
    api_key=AZURE_OPENAI_API_KEY,
    api_version="2024-04-01-preview",
    azure_endpoint=AZURE_OPENAI_ENDPOINT
)

# Streamlit App
st.title("PIM Role Access Request Portal")

# Authentication Flow
def authenticate_user():
    if "token" not in st.session_state:
        if st.button("Login with Microsoft"):
            auth_url = app.get_authorization_request_url(SCOPES, redirect_uri=REDIRECT_URI)
            logger.info("Generating authentication URL for user login")
            st.markdown(f'<meta http-equiv="refresh" content="0;URL={auth_url}">', unsafe_allow_html=True)
        return None
    return st.session_state["token"]

# Handle Callback
def handle_callback():
    query_params = st.query_params
    logger.debug(f"Query params received: {query_params}")
    if "code" in query_params:
        code = query_params.get("code")
        logger.info(f"Received authorization code: {code[:10]}...")  # Log partial code for security
        try:
            result = app.acquire_token_by_authorization_code(
                code, SCOPES, redirect_uri=REDIRECT_URI
            )
            if "access_token" in result:
                st.session_state["token"] = result["access_token"]
                st.session_state["user"] = result["id_token_claims"]
                logger.info(f"Successfully authenticated user: {result['id_token_claims'].get('name', 'Unknown')}")
                st.query_params.clear()  # Clear query params
                st.rerun()
            else:
                error_description = result.get("error_description", "Unknown error")
                logger.error(f"Authentication failed: {error_description}")
                st.error(f"Authentication failed: {error_description}")
                return None
        except Exception as e:
            logger.error(f"Error in token acquisition: {str(e)}")
            st.error(f"Error during authentication: {str(e)}")
            return None
    elif "error" in query_params:
        error = query_params.get("error")
        error_description = query_params.get("error_description", "Unknown error")
        logger.error(f"Authentication error from Azure AD: {error} - {error_description}")
        st.error(f"Authentication error: {error_description}")
        return None
    return st.session_state.get("token")

# Fetch PIM Roles from Microsoft Graph
def get_pim_roles(access_token):
    headers = {"Authorization": f"Bearer {access_token}"}
    endpoint = "https://graph.microsoft.com/v1.0/roleManagement/directory/roleDefinitions"
    logger.info("Fetching PIM roles from Microsoft Graph")
    try:
        response = requests.get(endpoint, headers=headers)
        if response.status_code == 200:
            logger.info("Successfully fetched PIM roles")
            return response.json().get("value", [])
        else:
            logger.error(f"Failed to fetch PIM roles: {response.status_code} - {response.text}")
            st.error("Failed to fetch PIM roles.")
            return []
    except Exception as e:
        logger.error(f"Error fetching PIM roles: {str(e)}")
        st.error("Failed to fetch PIM roles.")
        return []

# Draft Email using Azure OpenAI
def draft_email(user_name, user_id, role_name):
    prompt = f"""
    Draft a professional email requesting access to a PIM role. Include the user's name, user principal ID, and the role name. The email should be concise, polite, and addressed to an admin.

    User Name: {user_name}
    User Principal ID: {user_id}
    Role Name: {role_name}

    Example format:
    Subject: Request for {role_name} Role Access

    Dear Admin,

    I am requesting access to the {role_name} role. Below are my details:

    Name: {user_name}
    User Principal ID: {user_id}
    Role: {role_name}

    Please let me know if you need any additional information to process this request.

    Thank you,
    {user_name}
    """
    logger.info(f"Drafting email for user: {user_name}, role: {role_name}")
    try:
        response = openai_client.chat.completions.create(
            model=AZURE_OPENAI_DEPLOYMENT,
            messages=[{"role": "user", "content": prompt}]
        )
        email_content = response.choices[0].message.content
        logger.info("Successfully drafted email")
        return email_content
    except Exception as e:
        logger.error(f"Failed to draft email: {str(e)}")
        st.error("Failed to draft email.")
        return None

# Send Email via Microsoft Graph
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
    endpoint = "https://graph.microsoft.com/v1.0/me/sendMail"
    logger.info(f"Sending email to {ADMIN_EMAIL}")
    try:
        response = requests.post(endpoint, headers=headers, json=email_data)
        if response.status_code == 202:
            logger.info("Email sent successfully")
            return True
        else:
            logger.error(f"Failed to send email: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        logger.error(f"Failed to send email: {str(e)}")
        return False

# Main Logic
if "token" not in st.session_state:
    token = handle_callback() or authenticate_user()
else:
    token = st.session_state["token"]

if token:
    user_info = st.session_state["user"]
    user_name = user_info.get("name", "Unknown")
    user_id = user_info.get("oid", "Unknown")
    user_email = user_info.get("preferred_username", "Unknown")

    st.write(f"Welcome, {user_name}!")

    # Fetch and display PIM roles
    roles = get_pim_roles(token)
    role_names = [role["displayName"] for role in roles]
    selected_role = st.selectbox("Select PIM Role", role_names)

    if st.button("Request Access"):
        if selected_role:
            email_content = draft_email(user_name, user_id, selected_role)
            if email_content:
                st.text_area("Drafted Email", email_content, height=300)
                if send_email(token, user_email, email_content):
                    st.success("Request email sent to admin successfully!")
                else:
                    st.error("Failed to send email.")
        else:
            logger.warning("No role selected by user")
            st.error("Please select a role.")
