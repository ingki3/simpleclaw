import type { NextConfig } from "next";

/**
 * Next.js 16 (App Router) 설정.
 *
 * SimpleClaw Admin은 단일 운영자용 로컬 도구이므로 외부 이미지·번들 추적·텔레메트리는 모두 비활성화한다.
 */
const nextConfig: NextConfig = {
  reactStrictMode: true,
  productionBrowserSourceMaps: false,
};

export default nextConfig;
