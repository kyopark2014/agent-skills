import {
  useCallback,
  useEffect,
  useRef,
  useSyncExternalStore,
  useState,
  type DragEvent,
} from "react";
import { fileUploadService } from "../services/fileUploadService";

export interface AttachedImage {
  url: string;
  name: string;
  previewUrl: string;
}

export interface LoadedFile {
  path: string;
  name: string;
  size: number;
}

const ERROR_MESSAGE_DISPLAY_DURATION_MS = 5000;

function extensionFromMime(mime: string): string {
  if (mime === "image/jpeg") return ".jpg";
  if (mime === "image/webp") return ".webp";
  if (mime === "image/gif") return ".gif";
  return ".png";
}

export function isImageFile(file: File): boolean {
  if (file.type.startsWith("image/")) return true;
  return /\.(png|jpe?g|gif|webp)$/i.test(file.name);
}

export function normalizeImageFile(
  file: File,
  fallbackName = "pasted_screenshot",
): File {
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

export function collectClipboardImages(clipboardData: DataTransfer | null): File[] {
  if (!clipboardData) return [];

  const files: File[] = [];
  const seen = new Set<string>();

  // Clipboard File objects for the same paste often differ in name/lastModified
  // between items and files, so dedupe by size+type only.
  const pushUnique = (file: File) => {
    const key = `${file.size}:${file.type || "image/png"}`;
    if (seen.has(key)) return;
    seen.add(key);
    files.push(normalizeImageFile(file));
  };

  // Prefer items; files usually mirrors the same image with different metadata.
  for (const item of Array.from(clipboardData.items ?? [])) {
    if (!item.type.startsWith("image/")) continue;
    const blob = item.getAsFile();
    if (blob) pushUnique(blob);
  }

  if (files.length === 0) {
    for (const file of Array.from(clipboardData.files ?? [])) {
      if (isImageFile(file)) pushUnique(file);
    }
  }

  return files;
}


interface UploadSession {
  uploading: boolean;
  uploadError: string | null;
  attachments: AttachedImage[];
  loadedFiles: UploadLoadedFile[];
}

interface UploadLoadedFile {
  path: string;
  name: string;
  size: number;
}

const EMPTY_SESSION: UploadSession = {
  uploading: false,
  uploadError: null,
  attachments: [],
  loadedFiles: [],
};

const uploadSessions = new Map<string, UploadSession>();
const uploadSessionListeners = new Map<string, Set<() => void>>();

function uploadSessionKey(taskId: string | null | undefined): string {
  const trimmed = (taskId ?? "").trim();
  return trimmed || "__pending__";
}

function getUploadSession(key: string): UploadSession {
  return uploadSessions.get(key) ?? EMPTY_SESSION;
}

function setUploadSession(key: string, next: UploadSession) {
  if (
    !next.uploading &&
    !next.uploadError &&
    next.attachments.length === 0 &&
    next.loadedFiles.length === 0
  ) {
    uploadSessions.delete(key);
  } else {
    uploadSessions.set(key, next);
  }
  uploadSessionListeners.get(key)?.forEach((listener) => listener());
}

function updateUploadSession(
  key: string,
  updater: (prev: UploadSession) => UploadSession,
) {
  setUploadSession(key, updater(getUploadSession(key)));
}

function subscribeUploadSession(key: string, listener: () => void) {
  let listeners = uploadSessionListeners.get(key);
  if (!listeners) {
    listeners = new Set();
    uploadSessionListeners.set(key, listeners);
  }
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) uploadSessionListeners.delete(key);
  };
}

interface UseFileUploadOptions {
  disabled?: boolean;
  /** Keeps the in-progress upload when the chat screen unmounts. */
  taskId?: string | null;
}

export function seedTaskLoadedFile(taskId: string, file: LoadedFile) {
  const path = (file.path || "").trim();
  const name = (file.name || "").trim();
  if (!taskId.trim() || !path || !name) return;
  const key = uploadSessionKey(taskId);
  updateUploadSession(key, (session) => {
    const next = session.loadedFiles.filter((item) => item.path !== path);
    return {
      ...session,
      loadedFiles: [
        ...next,
        {
          path,
          name,
          size: Number.isFinite(file.size) && file.size > 0 ? file.size : 0,
        },
      ],
    };
  });
}

export function useFileUpload({ disabled = false, taskId = null }: UseFileUploadOptions = {}) {
  const sessionKey = uploadSessionKey(taskId);
  const sessionKeyRef = useRef(sessionKey);
  sessionKeyRef.current = sessionKey;
  const session = useSyncExternalStore(
    useCallback((listener) => subscribeUploadSession(sessionKey, listener), [sessionKey]),
    useCallback(() => getUploadSession(sessionKey), [sessionKey]),
    useCallback(() => getUploadSession(sessionKey), [sessionKey]),
  );
  const { uploading, uploadError, attachments, loadedFiles } = session;

  useEffect(() => {
    if (!uploadError) return;
    const key = sessionKey;
    const timer = window.setTimeout(() => {
      updateUploadSession(key, (prev) =>
        prev.uploadError ? { ...prev, uploadError: null } : prev,
      );
    }, ERROR_MESSAGE_DISPLAY_DURATION_MS);
    return () => window.clearTimeout(timer);
  }, [sessionKey, uploadError]);

  const clearUploadError = useCallback(() => {
    updateUploadSession(sessionKeyRef.current, (prev) =>
      prev.uploadError ? { ...prev, uploadError: null } : prev,
    );
  }, []);

  const uploadImageFile = useCallback(async (file: File) => {
    updateUploadSession(sessionKeyRef.current, (prev) => ({
      ...prev,
      uploading: true,
      uploadError: null,
    }));
    const previewUrl = URL.createObjectURL(file);
    try {
      const result = await fileUploadService.uploadImage(file);
      updateUploadSession(sessionKeyRef.current, (session) => {
        const prev = session.attachments;
        const nextValue = ((prev) => [
        ...prev,
        {
          url: result.url,
          name: result.file_name,
          previewUrl,
        },
      ])(prev);
        return { ...session, attachments: nextValue };
      });
    } catch (err) {
      URL.revokeObjectURL(previewUrl);
      console.error("Image upload failed", err);
      updateUploadSession(sessionKeyRef.current, (prev) => ({ ...prev, uploadError: "이미지 업로드에 실패했습니다. 다시 시도해 주세요." }));
    } finally {
      updateUploadSession(sessionKeyRef.current, (prev) => ({ ...prev, uploading: false }));
    }
  }, []);

  const uploadImageFiles = useCallback(
    async (files: File[]) => {
      if (files.length === 0 || disabled || getUploadSession(sessionKeyRef.current).uploading) return;
      for (const file of files) {
        await uploadImageFile(normalizeImageFile(file, "uploaded_image"));
      }
    },
    [disabled, uploadImageFile],
  );

  const uploadRagFile = useCallback(
    async (file: File, onComplete?: (message: string) => void) => {
      if (disabled || getUploadSession(sessionKeyRef.current).uploading) return;
      updateUploadSession(sessionKeyRef.current, (prev) => ({
        ...prev,
        uploading: true,
        uploadError: null,
      }));
      try {
        const result = await fileUploadService.uploadToRag(file);
        onComplete?.(result.message);
      } catch (err) {
        console.error("Document upload failed", err);
        updateUploadSession(sessionKeyRef.current, (prev) => ({ ...prev, uploadError: "파일 업로드에 실패했습니다. 다시 시도해 주세요." }));
      } finally {
        updateUploadSession(sessionKeyRef.current, (prev) => ({ ...prev, uploading: false }));
      }
    },
    [disabled],
  );

  const loadWorkspaceFiles = useCallback(
    async (files: File[]) => {
      if (files.length === 0 || disabled || getUploadSession(sessionKeyRef.current).uploading) return;
      updateUploadSession(sessionKeyRef.current, (prev) => ({
        ...prev,
        uploading: true,
        uploadError: null,
      }));
      try {
        for (const file of files) {
          const result = await fileUploadService.loadFile(file);
          updateUploadSession(sessionKeyRef.current, (session) => {
            const prev = session.loadedFiles;
            const nextValue = ((prev) => {
            const next = prev.filter((item) => item.path !== result.workspace_path);
            return [
              ...next,
              {
                path: result.workspace_path,
                name: result.file_name,
                size: file.size,
              },
            ];
          })(prev);
            return { ...session, loadedFiles: nextValue };
          });
        }
      } catch (err) {
        console.error("Load files failed", err);
        let detail = "파일 로드에 실패했습니다. 다시 시도해 주세요.";
        if (err instanceof Error) {
          const cause = (err as Error & { cause?: unknown }).cause;
          if (cause instanceof Error && cause.message) {
            detail = cause.message;
          } else if (err.message && !err.message.startsWith("Load file failed")) {
            detail = err.message;
          }
        }
        updateUploadSession(sessionKeyRef.current, (prev) => ({ ...prev, uploadError: detail }));
      } finally {
        updateUploadSession(sessionKeyRef.current, (prev) => ({ ...prev, uploading: false }));
      }
    },
    [disabled],
  );

  const removeAttachment = useCallback((url: string) => {
    updateUploadSession(sessionKeyRef.current, (session) => {
      const prev = session.attachments;
      const nextValue = ((prev) => {
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
    })(prev);
      return { ...session, attachments: nextValue };
    });
  }, []);

  const removeLoadedFile = useCallback((path: string) => {
    updateUploadSession(sessionKeyRef.current, (session) => {
      const prev = session.loadedFiles;
      const nextValue = ((prev) => prev.filter((item) => item.path !== path))(prev);
      return { ...session, loadedFiles: nextValue };
    });
  }, []);

  const attachExistingFile = useCallback(
    (file: LoadedFile) => {
      if (disabled) return;
      const path = (file.path || "").trim();
      const name = (file.name || "").trim();
      if (!path || !name) return;
      updateUploadSession(sessionKeyRef.current, (session) => {
        const prev = session.loadedFiles;
        const nextValue = ((prev) => {
        const next = prev.filter((item) => item.path !== path);
        return [
          ...next,
          {
            path,
            name,
            size: Number.isFinite(file.size) && file.size > 0 ? file.size : 0,
          },
        ];
      })(prev);
        return { ...session, loadedFiles: nextValue };
      });
    },
    [disabled],
  );

  const clearAttachments = useCallback(() => {
    updateUploadSession(sessionKeyRef.current, (session) => {
      const prev = session.attachments;
      const nextValue = ((prev) => {
      for (const item of prev) {
        if (item.previewUrl.startsWith("blob:")) {
          URL.revokeObjectURL(item.previewUrl);
        }
      }
      return [];
    })(prev);
      return { ...session, attachments: nextValue };
    });
    updateUploadSession(sessionKeyRef.current, (prev) => ({ ...prev, loadedFiles: [] }));
  }, []);

  const [dragOver, setDragOver] = useState(false);
  const dragDepthRef = useRef(0);

  const onDragEnter = useCallback(
    (e: DragEvent) => {
      if (disabled || getUploadSession(sessionKeyRef.current).uploading) return;
      if (![...e.dataTransfer.types].includes("Files")) return;
      e.preventDefault();
      dragDepthRef.current += 1;
      setDragOver(true);
    },
    [disabled],
  );

  const onDragOver = useCallback(
    (e: DragEvent) => {
      if (disabled || getUploadSession(sessionKeyRef.current).uploading) return;
      if (![...e.dataTransfer.types].includes("Files")) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
    },
    [disabled],
  );

  const onDragLeave = useCallback((e: DragEvent) => {
    if (![...e.dataTransfer.types].includes("Files") && dragDepthRef.current === 0) {
      return;
    }
    e.preventDefault();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) setDragOver(false);
  }, []);

  const onDrop = useCallback(
    async (e: DragEvent) => {
      e.preventDefault();
      dragDepthRef.current = 0;
      setDragOver(false);
      if (disabled || getUploadSession(sessionKeyRef.current).uploading) return;
      const imageFiles = Array.from(e.dataTransfer.files ?? []).filter(isImageFile);
      await uploadImageFiles(imageFiles);
    },
    [disabled, uploadImageFiles],
  );

  return {
    uploading,
    uploadError,
    attachments,
    loadedFiles,
    dragOver,
    isUploading: () => getUploadSession(sessionKeyRef.current).uploading,
    clearUploadError,
    uploadImageFiles,
    uploadRagFile,
    loadWorkspaceFiles,
    removeAttachment,
    removeLoadedFile,
    attachExistingFile,
    clearAttachments,
    onDragEnter,
    onDragOver,
    onDragLeave,
    onDrop,
  };
}
