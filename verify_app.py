import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import datetime
import io
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter

# --- GOOGLE DRIVE IMPORTS ---
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# --- SETUP: CONNECT TO GOOGLE SHEETS & MAPS ---
st.set_page_config(page_title="Verify - Cloud MVP", layout="wide")

# Initialize Geocoder
geolocator = Nominatim(user_agent="verify_mvp_app")
geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1)

# Connect to Sheets
conn = st.connection("gsheets", type=GSheetsConnection)

# --- HELPER: GOOGLE DRIVE UPLOADER ---
def upload_to_drive(file_obj, filename):
    # 1. Authenticate using the same secrets as Sheets
    creds_dict = dict(st.secrets["connections"]["gsheets"])
    creds = service_account.Credentials.from_service_account_info(
        creds_dict, 
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    
    # 2. Build the Drive Service
    drive_service = build('drive', 'v3', credentials=creds)
    
    # 3. Define File Metadata
    # Make sure to put your ACTUAL folder ID in the quotes below if using parents
    file_metadata = {
        "name": filename,
        "parents": "1d_Z1xkCj382X02v8WSk56DsaSC4jj2vq",
        "mimeType": "image/jpeg"
    }
    
    # 4. Convert Streamlit file to a BytesIO stream
    media = MediaIoBaseUpload(io.BytesIO(file_obj.getvalue()), mimetype='image/jpeg')
    
    # 5. Upload
    file = drive_service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id'
    ).execute()
    file_id = file.get('id')
    
    # 6. Make Public (So Streamlit can see it)
    drive_service.permissions().create(
        fileId=file_id,
        body={'role': 'reader', 'type': 'anyone'}
    ).execute()
    
    # 7. Return the "View" URL
    return f"https://drive.google.com/uc?id={file_id}"

def get_data():
    df = conn.read(worksheet="jobs", ttl=0)
    df['lat'] = pd.to_numeric(df['lat'], errors='coerce')
    df['lon'] = pd.to_numeric(df['lon'], errors='coerce')
    df['id'] = pd.to_numeric(df['id']).fillna(0).astype(int)
    return df

def update_job_status(job_id, evidence_file):
    df = get_data()
    clean_id = int(job_id)
    
    # --- NEW: UPLOAD TO CLOUD INSTEAD OF LOCAL DISK ---
    try:
        # Create a clean filename
        filename = f"job_{clean_id}_evidence.jpg"
        
        # Upload to Google Drive and get the URL
        image_url = upload_to_drive(evidence_file, filename)
        
        # Update Sheets with the URL
        job_index = df[df['id'] == clean_id].index
        if not job_index.empty:
            idx = job_index[0]
            df.at[idx, 'status'] = 'Complete'
            df.at[idx, 'evidence'] = image_url 
            df.at[idx, 'timestamp'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.update(worksheet="jobs", data=df)
            st.cache_data.clear()
            
    except Exception as e:
        st.error(f"Upload Failed: {e}")

def add_new_job(address, job_type, bounty, instructions):
    try:
        location = geolocator.geocode(address)
    except:
        location = None
    
    if location:
        df = get_data()
        new_id = df['id'].max() + 1 if not df.empty else 101
        
        new_row = pd.DataFrame([{
            "id": int(new_id),
            "address": location.address,
            "lat": location.latitude,
            "lon": location.longitude,
            "type": job_type,
            "bounty": bounty,
            "status": "Open",
            "instructions": instructions,
            "evidence": "",
            "timestamp": ""
        }])
        
        updated_df = pd.concat([df, new_row], ignore_index=True)
        conn.update(worksheet="jobs", data=updated_df)
        st.cache_data.clear()
        return True
    return False

# --- APP INTERFACE ---
st.sidebar.header("🔐 User Simulator")
user_role = st.sidebar.radio("Who are you?", ["Client (Insurance Co)", "Field Agent (Verifier)"])

df = get_data()

if user_role == "Client (Insurance Co)":
    st.title("Verify | Client Portal (Cloud)")
    col1, col2 = st.columns([1, 2])
    
    with col1:
        st.subheader("📍 Dispatch New Job")
        with st.form("new_job_form"):
            addr = st.text_input("Property Address")
            j_type = st.selectbox("Job Type", ["Storm Damage Check", "Occupancy Verification"])
            instr = st.text_area("Instructions")
            price = st.number_input("Bounty Offer ($)", min_value=15, value=35, step=5)
            
            if st.form_submit_button("🚀 Dispatch"):
                if addr:
                    with st.spinner("Verifying Address..."):
                        if add_new_job(addr, j_type, price, instr):
                            st.success("Job Dispatched!")
                            st.rerun()
                        else:
                            st.error("Address not found.")

    with col2:
        st.subheader("🗺️ Live Operations Map")
        st.map(df[['lat', 'lon']].dropna())
        st.divider()
        st.subheader("📂 Completed Reports")
        
        completed_jobs = df[df['status'] == 'Complete']
        if completed_jobs.empty:
            st.info("No reports ready yet.")
        else:
            for index, job in completed_jobs.iterrows():
                with st.expander(f"✅ Report #{job['id']} - {job['address']}"):
                    c1, c2 = st.columns(2)
                    with c1:
                        # NEW: Check if it's a URL (Cloud) or Path (Local Legacy)
                        evidence_path = str(job['evidence'])
                        if "http" in evidence_path:
                            st.image(evidence_path, caption="Cloud Evidence", width=None)
                        elif evidence_path and evidence_path != "nan":
                            st.warning("Legacy local file (cannot view in cloud).")
                    with c2:
                        st.write(f"**Verified At:** {job['timestamp']}")
                        st.write(f"**Target Location:** {job['lat']}, {job['lon']}")

else:
    st.title("Verify | Field Agent App")
    open_jobs = df[df['status'] == 'Open']
    
    if open_jobs.empty:
        st.warning("No gigs available.")
    else:
        for index, job in open_jobs.iterrows():
            with st.container(border=True):
                c1, c2 = st.columns([3, 1])
                with c1:
                    st.write(f"**{job['type']}**")
                    st.caption(f"📍 {job['address']}")
                with c2:
                    if st.button("📸 START", key=f"start_{job['id']}"):
                        st.session_state['active_job'] = int(job['id'])

    if 'active_job' in st.session_state:
        st.divider()
        st.write(f"Active Job ID: {st.session_state['active_job']}")
        picture = st.camera_input("Take Proof")
        
        if picture:
            if st.button("Submit Evidence"):
                with st.spinner("Uploading to Google Drive..."):
                    update_job_status(st.session_state['active_job'], picture)
                    del st.session_state['active_job']
                    st.success("Uploaded! Check Client Dashboard.")
                    st.rerun()
