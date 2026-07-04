# ResQNet — How to Run Guide

This guide details the exact steps to configure, run, and test ResQNet under two different operational scenarios:
1. **Local System Setup** (development, testing, and debugging on localhost)
2. **Live System Setup** (production deployment using Render/Railway, Vercel, and Neon Postgres)

---

## 💻 Condition 1: Running Everything on Local System

Use this configuration to run and test all dashboards and the backend server on your local machine.

### 1. Configure the Backend Environment
1. Navigate to the `backend/` directory.
2. Create a `.env` file by copying the template:
   ```bash
   cp env.example .env
   ```
3. Open `backend/.env` and populate the variables:
   * `DATABASE_URL`: Set this to your local or developer Neon Postgres connection string.
     * Example: `postgresql://neondb_owner:password@ep-host-pooler.neon.tech/neondb?sslmode=require`
   * `SESSION_TOKEN_WEBAPP_URL`: (Optional) Leave empty to disable responder email notifications during local testing.
   * `API_KEY`: (Optional) Leave blank for development mode (auth disabled).

### 2. Configure the Frontend Environment
1. Navigate to the `frontend/` directory.
2. Check or create `frontend/.env` (source of truth for configuration) and configure it for local URLs:
   ```env
   BACKEND_URL=http://localhost:8000
   WS_URL=ws://localhost:8000/ws/live
   # Firebase configuration details...
   ```
3. Run the generator script to compile and update `config.js` across all dashboard subfolders:
   ```bash
   node frontend/generate-config.js
   ```

### 3. Start the Project
* **Option A: One-Command Script** (Recommmended)
  Double-click or run from the project root:
  ```bash
  start.bat        # Windows
  # OR
  ./start.sh       # macOS / Linux
  ```
  This command starts the FastAPI backend, serves the dashboards using python HTTP server, and automatically launches the dashboards in your web browser.

* **Option B: Manual Startup**
  1. Start the FastAPI backend:
     ```bash
     cd backend
     uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
     ```
  2. Serve the dashboards in separate terminals:
     ```bash
     # Trial Dashboard (Port 5500)
     cd Trial_Dashboard && python -m http.server 5500
     
     # User Dashboard (Port 5501)
     cd frontend/user_dashboard && python -m http.server 5501
     
     # Responder Dashboard (Port 5502)
     cd frontend/responder_dashboard && python -m http.server 5502
     ```

### 4. Running the Integration Test Suite
To verify database persistence and endpoints on the local backend:
```bash
python scratch/test_user_flow.py
```

### 5. Running the Device Simulator
To seed a test user and run the device simulator:

**For Live System:**
```bash
python simulator/seed_simulator_user.py --url https://resqnet-gti8.onrender.com --uid #
python simulator/simulator.py --demo --id # --url https://resqnet-gti8.onrender.com/device/update
```

**For Local System:**
```bash
python seed_simulator_user.py --url http://127.0.0.1:8000 --uid #
python simulator.py --demo --id # --url http://127.0.0.1:8000/device/update
```

---

## 🚀 Condition 2: Running Everything on Live System

This section details the exact, step-by-step production configuration for ResQNet using your live cloud links.

### 1. Deploy the Google Apps Script (Email Dispatch & Session Manager)
Before configuring the backend, you must deploy the Google Apps Script Web App so that you have its endpoint URL:

1. Open **Google Drive** and locate your Google Sheet (ID: `1J3t8UhsigJrw9BKgV6ya6U8hTUVhiSf3anFtoUcg4MA`).
2. Go to [script.google.com](https://script.google.com) and open your standalone Apps Script project, or click **Extensions > Apps Script** from inside your Google Sheet.
3. Paste the contents of `backend/Session Token.gs` into the script editor.
4. Ensure the spreadsheet ID is defined at the top of the file:
   ```javascript
   const SHEET_ID = "1J3t8UhsigJrw9BKgV6ya6U8hTUVhiSf3anFtoUcg4MA";
   ```
5. **Authorize the script to send emails**:
   * Paste this temporary helper function at the bottom of the script:
     ```javascript
     function triggerAuth() {
       MailApp.sendEmail("test@example.com", "auth verification", "granting permission");
     }
     ```
   * Save the script. Select `triggerAuth` in the toolbar dropdown and click **Run**.
   * When prompted, click **Review permissions**, select your Google Account, click **Advanced**, click **Go to Untitled project (unsafe)**, and select **Allow**.
   * Once authorized, delete the `triggerAuth` function from your script.
6. **Deploy the Web App**:
   * Click **Deploy > Manage deployments > Edit** (or **New deployment**).
   * Configure the settings:
     * **Execute as**: `Me`
     * **Who has access**: `Anyone`
   * Click **Deploy**.
   * Copy the **Web App URL** (e.g., `https://script.google.com/macros/s/.../exec`).

---

### 2. Configure and Deploy the Backend on Render
1. Open your **Render Dashboard** and select your active web service (`resqnet-gti8`).
2. Navigate to the **Environment** tab.
3. Set the following environment variables:
   * `DATABASE_URL`: `postgresql://neondb_owner:npg_scheIRW70pzU@ep-twilight-feather-aoafhlvv-pooler.c-2.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require`
   * `SESSION_TOKEN_WEBAPP_URL`: `https://script.google.com/macros/s/AKfycbxGwq38-jfQ4OOnO-5CvOO2k_1jTMhq0hGCPsU7AiWLkA0EFMIPLRSIVBIRbXxHawop/exec`
   * `CORS_ORIGINS`: `https://aksh.is-a.dev,http://localhost:5500,http://localhost:5501,http://localhost:5502`
   * `API_KEY`: *(Optional: Set a hexadecimal security key if you want to enable authentication on the IoT endpoints)*
4. Save the changes. Render will automatically initiate a new deploy.
5. Confirm the deployment finishes and the logs display `Postgres : ENABLED` and `CORS origins allowed`.

---

### 3. Compile and Deploy the Frontends (GitHub Pages)
Your static frontends (User Dashboard, Responder Dashboard, Trial Dashboard) are hosted on your custom domain `https://aksh.is-a.dev/resqnet`.

1. Open your local project directory.
2. Open `frontend/.env` and update the parameters to point to the live Render backend:
   ```env
   BACKEND_URL=https://resqnet-gti8.onrender.com
   WS_URL=wss://resqnet-gti8.onrender.com/ws/live
   ```
3. Run the generator script to compile and push the live URLs into `config.js` across all frontend subfolders:
   ```bash
   node frontend/generate-config.js
   ```
4. Commit the changes and push them to your GitHub repository:
   ```bash
   git add .
   git commit -m "Configure frontend build to use live Render URLs"
   git push origin main
   ```
5. Once your static host rebuilds (about 30-60 seconds), your live portals at `https://aksh.is-a.dev` will be fully synchronized with the live Render backend.

---

### 4. Running a Live Simulation
To verify the entire live environment, use the Python simulator to register a user and run a live tracking path.

1. **Seed a new User Profile & Device**:
   Replace `#` with your custom Firebase User UID (e.g., `3fIPL5Y3MQTMf4q857zub1GgfN62`) to create a profile and default device registered to them in the live Neon Postgres database:
   ```bash
   python simulator/seed_simulator_user.py --url https://resqnet-gti8.onrender.com --uid 3fIPL5Y3MQTMf4q857zub1GgfN62
   ```
   *This outputs your newly registered simulator Device ID (e.g., `3fIPL5Y3MQTMf4q857zub1GgfN62_89172`).*

2. **Launch the Live Simulator**:
   Run the realistic GPS tracker using the returned Device ID:
   ```bash
   python simulator/simulator.py --demo --id 3fIPL5Y3MQTMf4q857zub1GgfN62_89172 --url https://resqnet-gti8.onrender.com/device/update
   ```
3. **Verify Alert Dispatch**:
   * The simulator will automatically trigger a panic signal at the 10-second mark.
   * Verify a new row is appended to your Google Sheet (`EmergencySessions`).
   * Open your email (`kumaraksh1107@gmail.com`) and verify you received the tracking alert.
   * Click the tracking link in the email to open the live Responder Dashboard at `https://aksh.is-a.dev` and confirm you see the real-time map trail updating.
   * Let the simulator run to `110s` to verify that the reset signal successfully resolves the incident on both Google Sheets and Neon Postgres.
