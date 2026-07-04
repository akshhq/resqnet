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

---

## 🚀 Condition 2: Running Everything on Live System

Use this configuration to deploy ResQNet in a live cloud production environment.

### 1. Deploy the Backend & Database
1. Create a server instance on **Render** or **Railway**.
2. Connect your GitHub repository to the instance (deploy directory: `backend`).
3. Set the following environment variables in your hosting provider's settings:
   * `DATABASE_URL`: Your production Neon Postgres connection string.
   * `SESSION_TOKEN_WEBAPP_URL`: The URL of your deployed Google Apps Script (Responder Email Dispatcher).
   * `API_KEY`: Set a secure 32-character hexadecimal string to protect IoT routes.
     * Generate one via Python: `python -c "import secrets; print(secrets.token_hex(32))"`
   * `CORS_ORIGINS`: Comma-separated list of your live frontend domains.
     * Example: `https://your-dashboard.vercel.app,https://your-responder.vercel.app`
4. Deploy the service.

### 2. Configure and Deploy the Frontends
1. Open the project on your local machine and navigate to the `frontend/` directory.
2. Edit `frontend/.env` with your live production details:
   ```env
   BACKEND_URL=https://your-backend-service.onrender.com
   WS_URL=wss://your-backend-service.onrender.com/ws/live
   # Firebase configuration details matching your Firebase console...
   ```
3. Run the generator script to compile and update `config.js` with production settings:
   ```bash
   node frontend/generate-config.js
   ```
4. Deploy the static directories (`Trial_Dashboard/`, `frontend/user_dashboard/`, and `frontend/responder_dashboard/`) to your static hosting provider (e.g., **Vercel**, **Netlify**, or **GitHub Pages**).

### 3. Deploy Google Apps Scripts
1. Paste the code from `apps_script/Emergency_Session_Backend.gs` into a Google Apps Script project.
2. Deploy the project as a Web App:
   * **Execute as**: `Me`
   * **Who has access**: `Anyone`
3. Copy the Web App Executable URL and save it as `SESSION_TOKEN_WEBAPP_URL` in the FastAPI backend's environment settings.
