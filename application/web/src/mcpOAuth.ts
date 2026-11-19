import { api } from "./api";

export type OAuthMcpProvider = "notion";

/**
 * Connect a hosted MCP (Notion) via a server-driven OAuth popup.
 * The popup is opened synchronously (popup blockers) and navigated once the
 * authorize URL is known; the callback page posts {type:"<provider>-oauth"} back.
 * `write` requests the second (generate/edit) scope tier where the provider has one.
 */
export type OAuthConnectResult = "connected" | "manual";

export async function connectMcpOAuth(
  provider: OAuthMcpProvider,
  opts: { write?: boolean } = {},
): Promise<OAuthConnectResult> {
  const popup = window.open("about:blank", `${provider}-oauth`, "width=520,height=720");
  if (!popup) throw new Error("팝업이 차단되었습니다. 팝업을 허용해 주세요.");

  let url: string;
  let manual = false;
  try {
    ({ url, manual = false } = await api.getOAuthMcpAuthorizeUrl(
      provider,
      Boolean(opts.write),
    ));
  } catch (err) {
    popup.close();
    throw err;
  }
  popup.location.href = url;

  // Manual paste-back path (unused while Canva is disabled; Notion uses auto callback).
  if (manual) return "manual";

  await new Promise<void>((resolve, reject) => {
    let settled = false;
    const finish = (fn: () => void) => {
      if (settled) return;
      settled = true;
      window.removeEventListener("message", onMessage);
      window.clearInterval(timer);
      fn();
    };
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;
      const data = event.data as { type?: string; ok?: boolean; message?: string };
      if (data?.type !== `${provider}-oauth`) return;
      finish(() =>
        data.ok ? resolve() : reject(new Error(data.message || "연결 실패")),
      );
    };
    window.addEventListener("message", onMessage);
    const timer = window.setInterval(() => {
      if (popup.closed) {
        finish(() => reject(new Error("연결 창이 닫혔습니다.")));
      }
    }, 500);
  });
  return "connected";
}
