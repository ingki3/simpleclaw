/**
 * 루트(`/`) — Admin 2.0 진입 페이지.
 *
 * S0 의 hello-world 골격 + S1 디자인 시스템 카탈로그 링크.
 * 실제 라우팅·내비게이션은 S2 (App Shell) 에서 정의한다.
 */
import Link from "next/link";

export default function HomePage() {
  return (
    <main
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "flex-start",
        gap: "12px",
        padding: "48px",
      }}
    >
      <h1 style={{ fontSize: "28px", fontWeight: 600, margin: 0 }}>
        SimpleClaw Admin 2.0
      </h1>
      <p style={{ margin: 0, color: "#555" }}>
        S0 scaffold is live. Design system (S1) and app shell (S2) follow.
      </p>
      <code
        data-testid="scaffold-marker"
        style={{
          fontSize: "13px",
          padding: "4px 8px",
          borderRadius: "4px",
          background: "#f3f4f6",
          color: "#111",
        }}
      >
        web/admin-2 — BIZ-111
      </code>
      <Link
        href="/design-system"
        style={{
          fontSize: "14px",
          padding: "8px 14px",
          borderRadius: "8px",
          background: "var(--primary, #5b6cf6)",
          color: "var(--primary-foreground, #fff)",
          textDecoration: "none",
        }}
      >
        BIZ-112 — Design system catalog →
      </Link>
    </main>
  );
}
