import type { NextConfig } from "next";

/**
 * Next.js 16 (App Router) 설정 — Admin 2.0 스캐폴드 (S0).
 *
 * SimpleClaw Admin 2.0은 단일 운영자용 로컬 도구이므로
 * 외부 이미지·번들 추적·텔레메트리는 모두 비활성화한다.
 * 본 설정은 기존 `web/admin/next.config.ts`의 컨벤션을 그대로 따른다.
 */
const nextConfig: NextConfig = {
  reactStrictMode: true,
  productionBrowserSourceMaps: false,
};

export default nextConfig;
