/**
 * 루트(`/`) — Admin 2.0 hello-world 페이지 (S0 스캐폴드).
 *
 * S0 의 DoD 는 "dev server 가 hello-world 페이지를 렌더한다" 이므로
 * 본 페이지는 빌드/린트/테스트 인프라가 살아 있는지만 검증한다.
 * 실제 라우팅·내비게이션은 S2 (App Shell) 에서 정의한다.
 */
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
    </main>
  );
}
