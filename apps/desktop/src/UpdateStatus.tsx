import { useState } from "react";
import { getVersion } from "@tauri-apps/api/app";
import { isTauri } from "@tauri-apps/api/core";

import { checkForUpdates, type UpdateCheckResult } from "./lib/updates";

export default function UpdateStatus() {
  const [checking, setChecking] = useState(false);
  const [result, setResult] = useState<UpdateCheckResult | null>(null);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);

  async function runCheck() {
    setChecking(true);
    setError("");
    setCopied(false);
    try {
      const currentVersion = isTauri() ? await getVersion() : "0.1.0";
      const next = await checkForUpdates(currentVersion);
      setResult(next);
      setExpanded(true);
    } catch (cause) {
      setResult(null);
      setExpanded(true);
      setError(cause instanceof Error ? cause.message : "检查更新失败");
    } finally {
      setChecking(false);
    }
  }

  async function copyReleasePage() {
    if (!result?.release_page_url) return;
    await navigator.clipboard.writeText(result.release_page_url);
    setCopied(true);
  }

  return (
    <aside className={`update-status ${expanded ? "update-status--expanded" : ""}`} aria-live="polite">
      <button
        className="update-status__trigger"
        type="button"
        onClick={() => (expanded ? setExpanded(false) : void runCheck())}
        disabled={checking}
        title="仅在你点击时检查 GitHub Release；不会自动下载或安装"
      >
        {checking ? "检查中…" : result?.update_available ? "发现更新" : "检查更新"}
      </button>

      {expanded ? (
        <div className="update-status__panel">
          <div className="update-status__header">
            <strong>受控更新检查</strong>
            <button type="button" onClick={() => setExpanded(false)} aria-label="关闭更新面板">×</button>
          </div>

          {error ? <p className="update-status__error">{error}</p> : null}

          {result ? (
            <>
              <dl>
                <div><dt>当前版本</dt><dd>{result.current_version}</dd></div>
                <div><dt>最新版本</dt><dd>{result.latest_version ?? "未知"}</dd></div>
                <div><dt>发布证据</dt><dd>{result.trusted ? "一致" : "未通过"}</dd></div>
                <div><dt>Engine</dt><dd>{result.engine_version ?? "未知"}</dd></div>
              </dl>

              {result.verification_issues.length ? (
                <div className="update-status__warning">
                  <strong>校验问题</strong>
                  <ul>
                    {result.verification_issues.map((issue) => <li key={issue}>{issue}</li>)}
                  </ul>
                </div>
              ) : null}

              <p className="update-status__summary">
                {result.update_available
                  ? result.trusted
                    ? "存在可用的新版本，发布证据完整。DeskAI 不会自动下载安装。"
                    : "检测到更高版本，但发布证据未通过校验，不建议下载安装。"
                  : "当前没有检测到更高版本。"}
              </p>

              {result.update_available && result.trusted && result.release_page_url ? (
                <button className="update-status__secondary" type="button" onClick={() => void copyReleasePage()}>
                  {copied ? "发布页地址已复制" : "复制官方发布页地址"}
                </button>
              ) : null}

              <button className="update-status__secondary" type="button" onClick={() => void runCheck()} disabled={checking}>
                重新检查
              </button>
            </>
          ) : null}

          <small>只有主动点击才联网；Local Only 模式会直接阻止检查。不会后台轮询、下载或静默安装。</small>
        </div>
      ) : null}
    </aside>
  );
}
