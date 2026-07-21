import { useEffect, useRef, useState } from "react";
import { formatBrandTitle } from "../formatBrandTitle";
import type { AppConfig, Task } from "../types";
import { ConfigDrawer } from "./ConfigDrawer";
import { LlmGatewayModal } from "./LlmGatewayModal";
import { TaskListItem } from "./TaskListItem";
import {
  GuardrailIcon,
  LlmGatewayIcon,
  LogoutIcon,
  McpIcon,
  MemoryIcon,
  ModelIcon,
  NewTaskIcon,
  SkillIcon,
  CloseIcon,
} from "./SidebarIcons";

type DrawerKind = "skill" | "mcp" | "model" | null;

interface Props {
  userId: string;
  tasks: Task[];
  activeTask: Task | null;
  config: AppConfig | null;
  drawer: DrawerKind;
  open: boolean;
  onClose: () => void;
  onNewTask: () => void;
  onSelectTask: (id: string) => void;
  onOpenDrawer: (kind: DrawerKind) => void;
  onCloseDrawer: () => void;
  onPatchTask: (taskId: string, patch: Partial<Task>) => void;
  onDeleteTask: (taskId: string) => void;
  onLogout: () => void;
  onRefreshConfig?: () => Promise<AppConfig | void> | AppConfig | void;
}

export function Sidebar({
  userId,
  tasks,
  activeTask,
  config,
  drawer,
  open,
  onClose,
  onNewTask,
  onSelectTask,
  onOpenDrawer,
  onCloseDrawer,
  onPatchTask,
  onDeleteTask,
  onLogout,
  onRefreshConfig,
}: Props) {
  const skillBtnRef = useRef<HTMLButtonElement>(null);
  const mcpBtnRef = useRef<HTMLButtonElement>(null);
  const modelBtnRef = useRef<HTMLButtonElement>(null);
  const [llmGatewayOpen, setLlmGatewayOpen] = useState(false);
  const skills = activeTask?.skills ?? config?.default_skills ?? [];
  const mcpServers = activeTask?.mcp_servers ?? config?.default_mcp_servers ?? [];
  const modelName = activeTask?.model_name ?? config?.default_model ?? "";
  const brandTitle = formatBrandTitle(config?.projectName ?? "agent", userId);
  const pinnedTasks = tasks.filter((task) => task.pinned);
  const regularTasks = tasks.filter((task) => !task.pinned);
  const llmGatewayEnabled = activeTask?.llm_gateway_enabled ?? false;
  const gatewayModels = config?.gateway_models ?? [];
  const modelOptions =
    llmGatewayEnabled && gatewayModels.length > 0 ? gatewayModels : (config?.models ?? []);

  useEffect(() => {
    if (!activeTask || !llmGatewayEnabled || gatewayModels.length === 0) return;
    if (gatewayModels.includes(modelName)) return;
    const next =
      config?.default_gateway_model && gatewayModels.includes(config.default_gateway_model)
        ? config.default_gateway_model
        : gatewayModels[0];
    onPatchTask(activeTask.id, { model_name: next });
  }, [
    activeTask,
    llmGatewayEnabled,
    gatewayModels,
    modelName,
    config?.default_gateway_model,
    onPatchTask,
  ]);

  function renderTask(task: Task, hidePinBadge = false) {
    return (
      <TaskListItem
        key={task.id}
        task={task}
        active={activeTask?.id === task.id}
        hidePinBadge={hidePinBadge}
        onSelect={() => onSelectTask(task.id)}
        onDelete={() => onDeleteTask(task.id)}
        onRename={(title) => onPatchTask(task.id, { title })}
        onTogglePin={() => onPatchTask(task.id, { pinned: !task.pinned })}
      />
    );
  }

  function toggleDrawer(kind: Exclude<DrawerKind, null>) {
    onOpenDrawer(drawer === kind ? null : kind);
  }

  return (
    <>
      <aside className={`sidebar${open ? " sidebar-panel-open" : ""}`}>
        <div className="sidebar-header">
          <div className="brand-row">
            <div className="brand">{brandTitle}</div>
            <div className="sidebar-header-actions">
              <button
                type="button"
                className="sidebar-close-btn"
                aria-label="메뉴 닫기"
                onClick={onClose}
              >
                <CloseIcon className="sidebar-icon" />
              </button>
              <button
                type="button"
                className="brand-logout-btn"
                aria-label="나가기"
                title="나가기"
                onClick={onLogout}
              >
                <LogoutIcon className="sidebar-icon" />
              </button>
            </div>
          </div>
        </div>

        <button type="button" className="sidebar-menu-btn" onClick={onNewTask}>
          <NewTaskIcon className="sidebar-icon" />
          <span>New task</span>
        </button>

        <div className="task-list">
          {pinnedTasks.length > 0 && (
            <div className="task-list-section">
              <div className="section-label">Pinned</div>
              {pinnedTasks.map((task) => renderTask(task, true))}
            </div>
          )}
          {regularTasks.length > 0 && (
            <div className="task-list-section">
              {pinnedTasks.length > 0 && <div className="section-label">Tasks</div>}
              {regularTasks.map((task) => renderTask(task))}
            </div>
          )}
        </div>

        <div className="sidebar-section">
          <div className="section-label">Configuration</div>
          <button
            ref={skillBtnRef}
            type="button"
            className={`sidebar-menu-btn${drawer === "skill" ? " is-active" : ""}`}
            aria-expanded={drawer === "skill"}
            aria-haspopup="dialog"
            onClick={() => toggleDrawer("skill")}
            disabled={!activeTask}
          >
            <SkillIcon className="sidebar-icon" />
            <span>Skill ({skills.length})</span>
          </button>
          <button
            ref={mcpBtnRef}
            type="button"
            className={`sidebar-menu-btn${drawer === "mcp" ? " is-active" : ""}`}
            aria-expanded={drawer === "mcp"}
            aria-haspopup="dialog"
            onClick={() => toggleDrawer("mcp")}
            disabled={!activeTask}
          >
            <McpIcon className="sidebar-icon" />
            <span>MCP ({mcpServers.length})</span>
          </button>
          <button
            ref={modelBtnRef}
            type="button"
            className={`sidebar-menu-btn${drawer === "model" ? " is-active" : ""}`}
            aria-expanded={drawer === "model"}
            aria-haspopup="dialog"
            title={modelName || "Model"}
            onClick={() => toggleDrawer("model")}
            disabled={!activeTask}
          >
            <ModelIcon className="sidebar-icon" />
            <span>{modelName || "Model"}</span>
          </button>
        </div>

        <div className="sidebar-section">
          <div className="section-label">Settings</div>
          <label className="sidebar-menu-btn settings-toggle">
            <GuardrailIcon className="sidebar-icon" />
            <span>Guardrail</span>
            <input
              type="checkbox"
              checked={activeTask?.guardrail_enabled ?? false}
              disabled={!activeTask}
              onChange={(e) =>
                activeTask &&
                onPatchTask(activeTask.id, { guardrail_enabled: e.target.checked })
              }
            />
          </label>
          <label className="sidebar-menu-btn settings-toggle">
            <MemoryIcon className="sidebar-icon" />
            <span>Memory</span>
            <input
              type="checkbox"
              checked={activeTask?.memory_enabled ?? true}
              disabled={!activeTask}
              onChange={(e) =>
                activeTask &&
                onPatchTask(activeTask.id, { memory_enabled: e.target.checked })
              }
            />
          </label>
          <button
            type="button"
            className={`sidebar-menu-btn${llmGatewayEnabled ? " is-active" : ""}`}
            disabled={!activeTask}
            onClick={() => setLlmGatewayOpen(true)}
          >
            <LlmGatewayIcon className="sidebar-icon" />
            <span>
              LLM Gateway{llmGatewayEnabled ? " (On)" : ""}
            </span>
          </button>
        </div>
      </aside>

      {drawer === "skill" && config && activeTask && (
        <ConfigDrawer
          title="Skill"
          options={config.skills}
          selected={skills}
          anchorEl={skillBtnRef.current}
          onChange={(next) => activeTask && onPatchTask(activeTask.id, { skills: next })}
          onClose={onCloseDrawer}
        />
      )}
      {drawer === "mcp" && config && activeTask && (
        <ConfigDrawer
          title="MCP"
          options={config.mcp_servers}
          selected={mcpServers}
          anchorEl={mcpBtnRef.current}
          onChange={(next) => activeTask && onPatchTask(activeTask.id, { mcp_servers: next })}
          onClose={onCloseDrawer}
        />
      )}
      {drawer === "model" && config && activeTask && (
        <ConfigDrawer
          title={llmGatewayEnabled ? "Model (LLM Gateway)" : "Model"}
          options={modelOptions}
          selected={modelName ? [modelName] : []}
          mode="single"
          anchorEl={modelBtnRef.current}
          onChange={(next) =>
            activeTask && next[0] && onPatchTask(activeTask.id, { model_name: next[0] })
          }
          onClose={onCloseDrawer}
        />
      )}

      {llmGatewayOpen && activeTask && (
        <LlmGatewayModal
          enabled={llmGatewayEnabled}
          onConfirmEnable={async (uiModels) => {
            const refreshed = await onRefreshConfig?.();
            const gatewayList =
              uiModels && uiModels.length > 0
                ? uiModels
                : refreshed && "gateway_models" in refreshed
                  ? (refreshed.gateway_models ?? [])
                  : (config?.gateway_models ?? []);
            const defaultGw =
              refreshed && "default_gateway_model" in refreshed
                ? refreshed.default_gateway_model
                : config?.default_gateway_model;
            const patch: Partial<Task> = { llm_gateway_enabled: true };
            if (gatewayList.length > 0 && !gatewayList.includes(modelName)) {
              patch.model_name =
                defaultGw && gatewayList.includes(defaultGw)
                  ? defaultGw
                  : gatewayList[0];
            }
            onPatchTask(activeTask.id, patch);
          }}
          onDisable={() =>
            onPatchTask(activeTask.id, { llm_gateway_enabled: false })
          }
          onClose={() => setLlmGatewayOpen(false)}
        />
      )}
    </>
  );
}
