import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { copyTextToClipboard } from "../copyToClipboard";

interface Props {
  x: number;
  y: number;
  markdown: string;
  onClose: () => void;
}

export function MarkdownContextMenu({ x, y, markdown, onClose }: Props) {
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onPointerUp(event: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        onClose();
      }
    }

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
      }
    }

    document.addEventListener("mouseup", onPointerUp);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mouseup", onPointerUp);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [onClose]);

  useEffect(() => {
    const menu = menuRef.current;
    if (!menu) return;

    const rect = menu.getBoundingClientRect();
    const padding = 8;
    let left = x;
    let top = y;

    if (left + rect.width > window.innerWidth - padding) {
      left = window.innerWidth - rect.width - padding;
    }
    if (top + rect.height > window.innerHeight - padding) {
      top = window.innerHeight - rect.height - padding;
    }

    menu.style.left = `${Math.max(padding, left)}px`;
    menu.style.top = `${Math.max(padding, top)}px`;
  }, [x, y]);

  function handleCopy(event: React.MouseEvent<HTMLButtonElement>) {
    event.preventDefault();
    event.stopPropagation();
    copyTextToClipboard(markdown);
    onClose();
  }

  return createPortal(
    <div
      ref={menuRef}
      className="markdown-context-menu"
      role="menu"
      style={{ left: x, top: y }}
      onMouseDown={(event) => event.stopPropagation()}
      onContextMenu={(event) => event.preventDefault()}
    >
      <button
        type="button"
        className="markdown-context-menu-item"
        role="menuitem"
        onMouseDown={handleCopy}
      >
        Copy markdown contents
      </button>
    </div>,
    document.body,
  );
}
