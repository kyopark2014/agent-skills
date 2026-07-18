import { FormEvent } from "react";
import { createPortal } from "react-dom";
import { formatBrandTitle } from "../formatBrandTitle";

interface Props {
  onSubmit: (userId: string) => void;
  error?: string | null;
  projectName?: string | null;
}

export function UserIdModal({ onSubmit, error, projectName }: Props) {
  const title = formatBrandTitle(projectName ?? "agent");

  function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const userId = String(form.get("user_id") ?? "").trim();
    if (userId) onSubmit(userId);
  }

  return createPortal(
    <div className="auth-screen">
      <div className="modal-overlay" role="dialog" aria-modal="true" aria-labelledby="user-id-title">
        <form className="modal" onSubmit={handleSubmit}>
          <h2 id="user-id-title">{title}</h2>
          <p>시작하려면 User ID를 입력하세요.</p>
          {error && <p className="modal-error">{error}</p>}
          <input name="user_id" placeholder="예: user01" autoFocus required />
          <div className="modal-actions">
            <button type="submit" className="send-btn">
              시작
            </button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  );
}
