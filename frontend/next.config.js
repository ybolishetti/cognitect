/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // No proxy rewrites — the frontend calls NEXT_PUBLIC_API_URL directly.
  // Local dev: http://localhost:8000. Vercel production: the Cloud Run URL
  // (see .env.example). This lets Vercel host the frontend independently of
  // wherever the backend runs.
};

module.exports = nextConfig;
