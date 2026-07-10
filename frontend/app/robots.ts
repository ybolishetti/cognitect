import type { MetadataRoute } from "next";

const siteUrl = "https://cognitect-six.vercel.app";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: "/",
      disallow: ["/plans/*", "/account", "/auth/*"],
    },
    sitemap: `${siteUrl}/sitemap.xml`,
  };
}
