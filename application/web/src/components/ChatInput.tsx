import {
  ClipboardEvent,
  DragEvent,
  FormEvent,
  KeyboardEvent,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { api } from "../api";

interface AttachedImage {
  url: string;
  name: string;
  previewUrl: string;
}

interface Props {
  disabled?: boolean;
  onSend: (text: string, files?: string[]) => void;
  onRagUploadComplete?: (message: string) => void;
}

const RAG_ACCEPT =
  ".pdf,.txt,.md,.csv,.doc,.docx,.ppt,.pptx,.xls,.xlsx,.html,.htm,.json,.py,.js";
const IMAGE_ACCEPT = "image/png,image/jpeg,image/webp,image/gif,.png,.jpg,.jpeg,.webp,.gif";
const MIN_INPUT_HEIGHT = 24;
const MAX_INPUT_HEIGHT = 160;

function extensionFromMime(mime: string): string {
  if (mime === "image/jpeg") return ".jpg";
  if (mime === "image/webp") return ".webp";
  if (mime === "image/gif") return ".gif";
  return ".png";
}

function isImageFile(file: File): boolean {
  if (file.type.startsWith("image/")) return true;
  return /\.(png|jpe?g|gif|webp)$/i.test(file.name);
}

function normalizeImageFile(file: File, fallbackName = "pasted_screenshot"): File {
  if (!isImageFile(file)) return file;
  const mime = file.type || "image/png";
  const ext = extensionFromMime(mime);
  const hasUsefulName =
    file.name &&
    file.name !== "image.png" &&
    file.name !== "image.jpg" &&
    file.name !== "blob";
  if (hasUsefulName) return file;
  return new File([file], `${fallbackName}${ext}`, { type: mime });
}

function collectClipboardImages(clipboardData: DataTransfer | null): File[] {
  if (!clipboardData) return [];

  const files: File[] = [];
  const seen = new Set<string>();

  const pushUnique = (file: File) => {
    const key = `${file.name}:${file.size}:${file.type}:${file.lastModified}`;
    if (seen.has(key)) return;
    seen.add(key);
    files.push(normalizeImageFile(file));
  };

  for (const item of Array.from(clipboardData.items ?? [])) {
    if (!item.type.startsWith("image/")) continue;
    const blob = item.getAsFile();
    if (blob) pushUnique(blob);
  }

  for (const file of Array.from(clipboardData.files ?? [])) {
    if (isImageFile(file)) pushUnique(file);
  }

  return files;
}

export function ChatInput({ disabled, onSend, onRagUploadComplete }: Props) {
  const [value, setValue] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuPosition, setMenuPosition] = useState<{
    left: number;
    top: number;
    width: number;
  } | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [attachments, setAttachments] = useState<AttachedImage[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const attachmentsRef = useRef<AttachedImage[]>([]);
  const uploadingRef = useRef(false);
  const dragDepthRef = useRef(0);
  const addWrapRef = useRef<HTMLDivElement>(null);
  const menuPortalRef = useRef<HTMLDivElement>(null);
  const addBtnRef = useRef<HTMLButtonElement>(null);
  const inputWrapRef = useRef<HTMLFormElement>(null);
  const ragInputRef = useRef<HTMLInputElement>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  attachmentsRef.current = attachments;
  uploadingRef.current = uploading;

  useEffect(() => {
    if (!uploadError) return;
    const timer = window.setTimeout(() => setUploadError(null), 5000);
    return () => window.clearTimeout(timer);
  }, [uploadError]);

  function adjustInputHeight() {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    const next = Math.min(
      Math.max(el.scrollHeight, MIN_INPUT_HEIGHT),
      MAX_INPUT_HEIGHT,
    );
    el.style.height = `${next}px`;
  }

  useLayoutEffect(() => {
    adjustInputHeight();
  }, [value]);

  useEffect(() => {
    return () => {
      for (const item of attachmentsRef.current) {
        if (item.previewUrl.startsWith("blob:")) {
          URL.revokeObjectURL(item.previewUrl);
        }
      }
    };
  }, []);

  function updateMenuPosition() {
    const rect = inputWrapRef.current?.getBoundingClientRect();
    if (!rect) return;
    setMenuPosition({
      left: rect.left,
      top: rect.top - 8,
      width: rect.width,
    });
  }

  useEffect(() => {
    if (!menuOpen) {
      setMenuPosition(null);
      return;
    }

    updateMenuPosition();
    window.addEventListener("resize", updateMenuPosition);
    window.addEventListener("scroll", updateMenuPosition, true);

    function onPointerDown(e: MouseEvent) {
      const target = e.target as Node;
      if (addWrapRef.current?.contains(target)) return;
      if (menuPortalRef.current?.contains(target)) return;
      setMenuOpen(false);
    }
    function onKeyDown(e: globalThis.KeyboardEvent) {
      if (e.key === "Escape") setMenuOpen(false);
    }

    document.addEventListener("mousedown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("resize", updateMenuPosition);
      window.removeEventListener("scroll", updateMenuPosition, true);
      document.removeEventListener("mousedown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [menuOpen]);

  function submit() {
    const text = value.trim();
    const files = attachments.map((item) => item.url);
    if ((!text && files.length === 0) || disabled || uploading) return;
    onSend(text, files);
    setValue("");
    setAttachments((prev) => {
      for (const item of prev) {
        if (item.previewUrl.startsWith("blob:")) {
          URL.revokeObjectURL(item.previewUrl);
        }
      }
      return [];
    });
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    submit();
  }

  async function uploadImageFile(file: File) {
    setUploading(true);
    setUploadError(null);
    const previewUrl = URL.createObjectURL(file);
    try {
      const result = await api.uploadFile(file);
      setAttachments((prev) => [
        ...prev,
        {
          url: result.url,
          name: result.file_name,
          previewUrl,
        },
      ]);
    } catch (err) {
      URL.revokeObjectURL(previewUrl);
      const message = err instanceof Error ? err.message : String(err);
      setUploadError(message);
    } finally {
      setUploading(false);
    }
  }

  async function uploadImageFiles(files: File[]) {
    if (files.length === 0 || disabled || uploadingRef.current) return;
    for (const file of files) {
      await uploadImageFile(normalizeImageFile(file, "uploaded_image"));
    }
  }

  async function onPaste(e: ClipboardEvent<HTMLTextAreaElement>) {
    if (disabled || uploadingRef.current) return;
    const imageFiles = collectClipboardImages(e.clipboardData);
    if (imageFiles.length === 0) return;

    e.preventDefault();
    await uploadImageFiles(imageFiles);
  }

  function onDragEnter(e: DragEvent<HTMLFormElement>) {
    if (disabled || uploading) return;
    if (![...e.dataTransfer.types].includes("Files")) return;
    e.preventDefault();
    dragDepthRef.current += 1;
    setDragOver(true);
  }

  function onDragOver(e: DragEvent<HTMLFormElement>) {
    if (disabled || uploading) return;
    if (![...e.dataTransfer.types].includes("Files")) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  }

  function onDragLeave(e: DragEvent<HTMLFormElement>) {
    if (![...e.dataTransfer.types].includes("Files") && dragDepthRef.current === 0) {
      return;
    }
    e.preventDefault();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) setDragOver(false);
  }

  async function onDrop(e: DragEvent<HTMLFormElement>) {
    e.preventDefault();
    dragDepthRef.current = 0;
    setDragOver(false);
    if (disabled || uploadingRef.current) return;

    const imageFiles = Array.from(e.dataTransfer.files ?? []).filter(isImageFile);
    await uploadImageFiles(imageFiles);
  }

  function removeAttachment(url: string) {
    setAttachments((prev) => {
      const next: AttachedImage[] = [];
      for (const item of prev) {
        if (item.url === url) {
          if (item.previewUrl.startsWith("blob:")) {
            URL.revokeObjectURL(item.previewUrl);
          }
          continue;
        }
        next.push(item);
      }
      return next;
    });
  }

  function openImageUpload() {
    setMenuOpen(false);
    setUploadError(null);
    imageInputRef.current?.click();
  }

  function openRagUpload() {
    setMenuOpen(false);
    setUploadError(null);
    ragInputRef.current?.click();
  }

  async function onImageSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []).filter(isImageFile);
    e.target.value = "";
    await uploadImageFiles(files);
  }

  async function onRagFileSelected(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file || disabled || uploading) return;

    setUploading(true);
    setUploadError(null);
    try {
      const result = await api.uploadToRag(file);
      onRagUploadComplete?.(result.message);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setUploadError(message);
    } finally {
      setUploading(false);
    }
  }

  const inputDisabled = disabled || uploading;
  const canSend = !inputDisabled && (value.trim().length > 0 || attachments.length > 0);

  const menu =
    menuOpen && menuPosition
      ? createPortal(
          <div
            ref={menuPortalRef}
            className="chat-add-menu chat-add-menu-portal"
            role="menu"
            style={{
              left: menuPosition.left,
              top: menuPosition.top,
              width: menuPosition.width,
            }}
          >
            <button
              type="button"
              className="chat-add-menu-item"
              role="menuitem"
              onClick={openImageUpload}
            >
              <span className="chat-add-menu-icon" aria-hidden="true">
                <svg width="16" height="16" viewBox="0 0 16 16">
                  <rect
                    x="2.5"
                    y="3.5"
                    width="11"
                    height="9"
                    rx="1.5"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.2"
                  />
                  <circle cx="6" cy="7" r="1.2" fill="currentColor" />
                  <path
                    d="M4.5 11.5 7 9l2 1.5 2.5-3 2 4"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </span>
              <span className="chat-add-menu-text">
                <span className="chat-add-menu-label">사진 첨부</span>
                <span className="chat-add-menu-desc">
                  이미지를 첨부하거나 Ctrl/⌘+V로 붙여넣기
                </span>
              </span>
            </button>
            <button
              type="button"
              className="chat-add-menu-item"
              role="menuitem"
              onClick={openRagUpload}
            >
              <span className="chat-add-menu-icon" aria-hidden="true">
                <svg width="16" height="16" viewBox="0 0 16 16">
                  <path
                    d="M4 2.5h5.5L12 5v8.5a.5.5 0 0 1-.5.5H4a.5.5 0 0 1-.5-.5v-11a.5.5 0 0 1 .5-.5Z"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.2"
                  />
                  <path
                    d="M9.5 2.5V5H12"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.2"
                  />
                </svg>
              </span>
              <span className="chat-add-menu-text">
                <span className="chat-add-menu-label">Upload to RAG</span>
                <span className="chat-add-menu-desc">
                  S3에 업로드하고 Knowledge Base 동기화
                </span>
              </span>
            </button>
          </div>,
          document.body,
        )
      : null;

  return (
    <div className="chat-input-area">
      {uploadError && (
        <div className="chat-upload-error" role="alert">
          {uploadError}
        </div>
      )}
      {uploading && (
        <div className="chat-upload-status" role="status">
          업로드 중...
        </div>
      )}
      <form
        className={`chat-input-wrap${dragOver ? " is-dragover" : ""}`}
        ref={inputWrapRef}
        onSubmit={onSubmit}
        onDragEnter={onDragEnter}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
      >
        <input
          ref={imageInputRef}
          type="file"
          className="chat-file-input"
          accept={IMAGE_ACCEPT}
          multiple
          onChange={onImageSelected}
          tabIndex={-1}
          aria-hidden="true"
        />
        <input
          ref={ragInputRef}
          type="file"
          className="chat-file-input"
          accept={RAG_ACCEPT}
          onChange={onRagFileSelected}
          tabIndex={-1}
          aria-hidden="true"
        />
        {attachments.length > 0 && (
          <div className="chat-attachments" aria-label="첨부 이미지">
            {attachments.map((item) => (
              <div key={item.url} className="chat-attachment">
                <img src={item.previewUrl} alt={item.name} />
                <button
                  type="button"
                  className="chat-attachment-remove"
                  aria-label={`${item.name} 제거`}
                  onClick={() => removeAttachment(item.url)}
                  disabled={inputDisabled}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}
        <textarea
          ref={textareaRef}
          className="chat-input"
          rows={1}
          placeholder="메시지를 입력하거나 이미지를 붙여넣으세요..."
          value={value}
          disabled={inputDisabled}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
        />
        <div className="chat-input-toolbar">
          <div className="chat-input-add-wrap" ref={addWrapRef}>
            <button
              ref={addBtnRef}
              type="button"
              className="chat-add-btn"
              aria-label="추가"
              aria-expanded={menuOpen}
              disabled={inputDisabled}
              onClick={() => setMenuOpen((open) => !open)}
            >
              <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
                <path
                  d="M8 3v10M3 8h10"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                />
              </svg>
            </button>
          </div>
          <button
            className="chat-send-btn"
            type="submit"
            aria-label="전송"
            disabled={!canSend}
          >
            <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
              <path
                d="M8 12.5V3.5M4.5 7 8 3.5 11.5 7"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
        </div>
      </form>
      {menu}
    </div>
  );
}
