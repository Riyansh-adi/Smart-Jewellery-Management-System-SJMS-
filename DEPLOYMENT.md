# Deploy SJMS: Render API + Vercel frontend

Deployment configuration is included in `render.yaml` and `vercel.json`. Push this repository to GitHub, then deploy each service from that same repository.

## 1. Secure the Atlas connection

The database password was shared in this conversation. Rotate the Atlas database user's password before deployment. Update the URI in your local `.env`, and use the new URI only in Render's private environment variables. Never put it in frontend code or `VITE_API_URL`.

In Atlas, open **Security → Network Access** and allow the outbound IP ranges shown by the Render service under **Connect → Outbound**. Render uses the ranges for the service's region unless dedicated outbound IPs are configured; Atlas supports IP/CIDR allow-list entries. See [Render outbound IP addresses](https://render.com/docs/outbound-ip-addresses) and [Atlas IP access lists](https://www.mongodb.com/docs/atlas/security/ip-access-list/).

## 2. Deploy the API to Render

1. Open the [Render Dashboard](https://dashboard.render.com/), choose **New → Blueprint**, and connect the GitHub repository.
2. Render will read `render.yaml` and create the `sjms-api` web service. Use the service settings from the blueprint: root directory `backend`, build command `pip install -r requirements.txt`, start command `uvicorn app.main:app --host 0.0.0.0 --port $PORT`, health check `/health`.
3. Provide the requested private values:
   - `MONGODB_URI`: your rotated MongoDB Atlas connection URI.
   - `CORS_ORIGINS`: temporarily `http://localhost:5173`; replace it with the deployed Vercel site origin after step 3.
4. Keep the generated `JWT_SECRET` private. Wait for Render's deploy to finish and copy the service URL, such as `https://sjms-api.onrender.com`.

For the live metal ticker, create an API key with [Metals.Dev](https://metals.dev/), then add `METALS_DEV_API_KEY` in the Render service's **Environment** settings and redeploy. Keep it on Render only; it must not be added to Vercel. The application refreshes the quote feed every five minutes by default (`METALS_RATES_REFRESH_SECONDS=300`). Metals.Dev's free plan currently includes 100 requests per month, which is not enough for continuous five-minute refresh; a 10,000-request plan supports about 8,640 requests for one continuously active service in a 30-day month. Increase the plan or set a longer refresh interval to fit your usage. The widget labels the prices as indicative international spot references, not local jeweller retail prices.

## 3. Deploy the frontend to Vercel

1. Open the [Vercel Dashboard](https://vercel.com/dashboard) and import the same GitHub repository.
2. Use the project root as the root directory and the Vite preset. Set build command to `npm run build` and output directory to `dist` (install command: `npm ci`). `vercel.json` includes the SPA rewrite so app routes resolve correctly.
3. Add this environment variable before deploying:
   - `VITE_API_URL` = `https://<your-render-service>.onrender.com/api`
4. Deploy and copy the production site origin, for example `https://your-project.vercel.app`.

## 4. Connect the deployed site to the API

In Render, open the API service's **Environment** settings and change `CORS_ORIGINS` to the exact Vercel origin only (scheme and host, no trailing path), such as `https://your-project.vercel.app`. Save and redeploy the API. If you later add a custom frontend domain, add that origin too.

## 5. Create the initial administrator

The seed command is explicit and should only be run once against the intended database. With Python dependencies installed locally and the rotated Atlas URI in `.env`, from PowerShell run:

```powershell
cd "D:\project\major project"
.\.venv\Scripts\Activate.ps1
cd backend
python -m app.seed
```

It creates `admin@sjms.demo` with temporary password `ChangeMe123!` if that user does not already exist. Change it immediately after signing in.

## 6. Verify

- API health: `https://<your-render-service>.onrender.com/health` should return `{"status":"ok"}`.
- Swagger UI: `https://<your-render-service>.onrender.com/docs`.
- Open the Vercel site, sign in, and verify inventory loads from the API.

Render's free web services sleep after 15 minutes without requests and can take about a minute to wake up. Render recommends paid instances for applications that need reliable availability. See [Render's free service limitations](https://render.com/docs/free). Vercel preview deployments are not included in `CORS_ORIGINS`; add a preview origin only when you need preview builds to call the API.
