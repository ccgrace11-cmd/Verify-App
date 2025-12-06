import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
import datetime
import io
import os
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter

# --- CLOUD STORAGE IMPORTS ---
from google.cloud import storage
from google.oauth2 import service_account

# --- SETUP: CONNECT TO GOOGLE SHEETS & MAPS ---
st.set_page_config(page_title="Verify - Cloud MVP", layout="wide")

# Initialize Geocoder
geolocator = Nominatim(user_agent="verify_mvp_app")
geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1)

# Connect to Sheets
conn = st.connection("gsheets", type=GSheetsConnection)

# --- HELPER: GOOGLE BUCKET UPLOADER ---
def upload_to_bucket(file_obj, filename):
    # 1. Authenticate
    creds_dict = dict(st.secrets["connections"]["gsheets"])
    creds = service_account.Credentials.from_service_account_info(creds_dict)
    
    # 2. Connect to GCS
    client = storage.Client(credentials=creds, project=creds_dict["project_id"])
    
    # 3. Get the Bucket
    # REPLACE WITH YOUR ACTUAL BUCKET NAME!
    bucket = client.bucket("verify-mvp-evidence")
    
    # 4. Create a "Blob" (The file placeholder)
    blob = bucket.blob(filename)
    
    # 5. Upload from RAM (Bytes)
    # Rewind the file pointer to the start just in case
    file_obj.seek(0)
    blob.upload_from_file(file_obj, content_type="image/jpeg")
    
    # 6. Return the Public URL
    return blob.public_url

# --- DATA FUNCTIONS ---
def get_data():
    df = conn.read(worksheet="jobs", ttl=0)
    df['lat'] = pd.to_numeric(df['lat'], errors='coerce')
    df['lon'] = pd.to_numeric(df['lon'], errors='coerce')
    df['id'] = pd.to_numeric(df['id']).fillna(0).astype(int)
    return df

def update_job_status(job_id, evidence_file):
    df = get_data()
    clean_id = int(job_id)
    
    try:
        filename = f"job_{clean_id}_evidence.jpg"
        
        # Upload to Bucket
        image_url = upload_to_bucket(evidence_file, filename)
        
        # Update Sheets
        job_index = df[df['id'] == clean_id].index
        if not job_index.empty:
            idx = job_index[0]
            df.at[idx, 'status'] = 'Complete'
            df.at[idx, 'evidence'] = image_url 
            df.at[idx, 'timestamp'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn.update(worksheet="jobs", data=df)
            st.cache_data.clear()
            return True
            
    except Exception as e:
        st.error(f"STAY ON SCREEN - Upload Failed: {e}")
        return False

def add_new_job(address, job_type, bounty, instructions):
    real_lat = 0.0
    real_lon = 0.0
    address_found = False
    
    try:
        location = geolocator.geocode(address)
        if location:
            real_lat = location.latitude
            real_lon = location.longitude
            address = location.address
            address_found = True
    except:
        pass
    
    df = get_data()
    new_id = df['id'].max() + 1 if not df.empty else 101
    
    new_row = pd.DataFrame([{
        "id": int(new_id),
        "address": address,
        "lat": real_lat,
        "lon": real_lon,
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
    return address_found

# --- APP INTERFACE ---
st.sidebar.header("🔐 User Simulator")
user_role = st.sidebar.radio("Who are you?", ["Client (Insurance Co)", "Field Agent (Verifier)"])

df = get_data()

if user_role == "Client (Insurance Co)":
    st.title("Verify | Client Portal (Bucket)")
    
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
                    with st.spinner("Processing..."):
                        found = add_new_job(addr, j_type, price, instr)
                        if found:
                            st.success("Job Dispatched!")
                        else:
                            st.warning("Job Dispatched (Map service busy).")
                        st.rerun()

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
                        evidence_path = str(job['evidence'])
                        if "http" in evidence_path:
                            st.image(evidence_path, caption="Verified Evidence", width=None)
                        elif evidence_path and evidence_path != "nan" and evidence_path != "":
                             st.warning("Legacy file.")
                        else:
                             st.info("No evidence.")
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
                with st.spinner("Uploading to Cloud Bucket..."):
                    success = update_job_status(st.session_state['active_job'], picture)
                    if success:
                        del st.session_state['active_job']
                        st.success("Uploaded! Check Client Dashboard.")
                        st.rerun()
