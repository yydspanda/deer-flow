"use client";

import { RotateCcwIcon, SlidersHorizontalIcon } from "lucide-react";
import { type ReactNode, useId } from "react";

import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import type {
  SocAnalysisExecutionOptions,
  SocCorpusWorkbenchRunControls,
} from "@/core/soc";

export const RUN_SETTINGS_STORAGE_KEY = "soc.corpus-validation.run-settings.v1";

export function availableCorpusRunSettings(
  value: SocAnalysisExecutionOptions,
  controls: SocCorpusWorkbenchRunControls,
): SocAnalysisExecutionOptions {
  const policyEnabled =
    value.tenant_policy_enabled && controls.tenant_policy_available;
  return {
    ...(value.refresh_normalization &&
    controls.normalization_review_available &&
    value.normalization_review_mode !== "off"
      ? { refresh_normalization: true }
      : {}),
    normalization_review_mode: controls.normalization_review_available
      ? value.normalization_review_mode
      : "off",
    tenant_policy_enabled: policyEnabled,
    tenant_policy_advisor_enabled:
      policyEnabled &&
      controls.tenant_policy_advisor_available &&
      value.tenant_policy_advisor_enabled,
    tenant_policy_signal_providers_enabled:
      policyEnabled &&
      controls.tenant_policy_signal_providers_available &&
      value.tenant_policy_signal_providers_enabled,
  };
}

export function readCorpusRunSettings(
  storageKey = RUN_SETTINGS_STORAGE_KEY,
): SocAnalysisExecutionOptions | null {
  try {
    const value: unknown = JSON.parse(
      window.sessionStorage.getItem(storageKey) ?? "null",
    );
    if (!value || typeof value !== "object") return null;
    const fields = value as Record<string, unknown>;
    if (
      !["off", "shadow", "apply"].includes(
        String(fields.normalization_review_mode),
      ) ||
      ![
        "tenant_policy_enabled",
        "tenant_policy_advisor_enabled",
        "tenant_policy_signal_providers_enabled",
      ].every((key) => typeof fields[key] === "boolean")
    )
      return null;
    if (
      !fields.tenant_policy_enabled &&
      (fields.tenant_policy_advisor_enabled ||
        fields.tenant_policy_signal_providers_enabled)
    )
      return null;
    return {
      ...(fields.refresh_normalization === true
        ? { refresh_normalization: true }
        : {}),
      normalization_review_mode:
        fields.normalization_review_mode as SocAnalysisExecutionOptions["normalization_review_mode"],
      tenant_policy_enabled: fields.tenant_policy_enabled as boolean,
      tenant_policy_advisor_enabled:
        fields.tenant_policy_advisor_enabled as boolean,
      tenant_policy_signal_providers_enabled:
        fields.tenant_policy_signal_providers_enabled as boolean,
    };
  } catch {
    return null;
  }
}

export function SocCorpusRunSettings({
  controls,
  value,
  onChange,
  title = "后续运行设置",
  resetTitle = "恢复部署默认设置",
  disabled = false,
  children,
}: {
  controls: SocCorpusWorkbenchRunControls;
  value: SocAnalysisExecutionOptions;
  onChange: (value: SocAnalysisExecutionOptions) => void;
  title?: string;
  resetTitle?: string;
  disabled?: boolean;
  children?: ReactNode;
}) {
  const controlId = useId();
  const readOnly = controls.can_configure === false;
  const items = [
    {
      label: "语义核对",
      checked: value.normalization_review_mode !== "off",
      available: controls.normalization_review_available,
      disabled: false,
      detail:
        value.normalization_review_mode === "shadow"
          ? "仅对比"
          : "补充标准事实",
      change: (checked: boolean) =>
        onChange({
          ...value,
          normalization_review_mode: checked ? "apply" : "off",
        }),
    },
    {
      label: "企业策略",
      checked: value.tenant_policy_enabled,
      available: controls.tenant_policy_available,
      disabled: false,
      detail: "总开关 · 含企业规则",
      change: (checked: boolean) =>
        onChange({
          ...value,
          tenant_policy_enabled: checked,
          tenant_policy_advisor_enabled:
            checked && value.tenant_policy_advisor_enabled,
          tenant_policy_signal_providers_enabled:
            checked && value.tenant_policy_signal_providers_enabled,
        }),
    },
    {
      label: "安全软件路径策略",
      checked: value.tenant_policy_signal_providers_enabled,
      available: controls.tenant_policy_signal_providers_available,
      disabled: !value.tenant_policy_enabled,
      detail: "企业策略子项",
      change: (checked: boolean) =>
        onChange({ ...value, tenant_policy_signal_providers_enabled: checked }),
    },
    {
      label: "LLM 策略建议",
      checked: value.tenant_policy_advisor_enabled,
      available: controls.tenant_policy_advisor_available,
      disabled: !value.tenant_policy_enabled,
      detail: "企业策略子项",
      change: (checked: boolean) =>
        onChange({ ...value, tenant_policy_advisor_enabled: checked }),
    },
  ];
  return (
    <section className="border-b px-5 py-3 md:px-7" aria-label={title}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-sm font-medium">
          <SlidersHorizontalIcon className="size-4" />
          {title}
        </h3>
        {readOnly && (
          <span className="text-muted-foreground text-xs">
            仅部署主机可修改
          </span>
        )}
        <Button
          variant="ghost"
          size="sm"
          disabled={readOnly || disabled}
          onClick={() =>
            onChange(availableCorpusRunSettings(controls.defaults, controls))
          }
          title={resetTitle}
        >
          <RotateCcwIcon className="size-3.5" />
          恢复默认
        </Button>
      </div>
      <div className="grid gap-x-8 gap-y-4 sm:grid-cols-2 xl:grid-cols-4">
        {items.map((item, index) => (
          <div key={item.label} className="flex min-w-0 items-start gap-3">
            <Switch
              id={`${controlId}-${index}`}
              className="mt-0.5 shrink-0"
              aria-label={item.label}
              aria-describedby={`${controlId}-${index}-detail`}
              checked={item.checked}
              disabled={
                disabled || readOnly || !item.available || item.disabled
              }
              onCheckedChange={item.change}
            />
            <div className="min-w-0">
              <label
                htmlFor={`${controlId}-${index}`}
                className="block cursor-pointer text-sm font-medium"
              >
                {item.label}
              </label>
              <p
                id={`${controlId}-${index}-detail`}
                className="text-muted-foreground mt-1 text-xs"
              >
                {!item.available
                  ? "当前部署未配置或未允许"
                  : item.disabled
                    ? "企业策略已关闭"
                    : item.detail}
              </p>
              {index === 0 && (
                <label
                  className="mt-2 flex cursor-pointer items-center gap-2 text-xs"
                  title="勾选后重新调用语义核对模型；不勾选时，同输入和同配置复用已保存事实，仍重新匹配最新经验与策略。"
                >
                  <input
                    type="checkbox"
                    className="accent-primary size-4 shrink-0"
                    aria-label="重新核对事实"
                    checked={value.refresh_normalization === true}
                    disabled={
                      disabled ||
                      readOnly ||
                      !item.available ||
                      value.normalization_review_mode === "off"
                    }
                    onChange={(event) =>
                      onChange({
                        ...value,
                        refresh_normalization: event.target.checked,
                      })
                    }
                  />
                  重新核对事实
                </label>
              )}
            </div>
          </div>
        ))}
      </div>
      {children}
    </section>
  );
}

export function SocRunOptionsSummary({
  value,
  title = "本次运行配置",
}: {
  value: SocAnalysisExecutionOptions;
  title?: string;
}) {
  return (
    <div
      className="flex flex-wrap gap-x-4 gap-y-1 border-t px-5 py-2 text-xs md:px-7"
      aria-label={title}
    >
      <span className="font-medium">{title}</span>
      <span>
        语义核对：
        {value.normalization_review_mode === "apply"
          ? "开启"
          : value.normalization_review_mode === "shadow"
            ? "仅对比"
            : "关闭"}
      </span>
      {value.refresh_normalization && <span>事实整理：重新核对</span>}
      <span>企业策略：{value.tenant_policy_enabled ? "开启" : "关闭"}</span>
      <span>
        安全路径：
        {value.tenant_policy_signal_providers_enabled ? "开启" : "关闭"}
      </span>
      <span>
        LLM 策略建议：{value.tenant_policy_advisor_enabled ? "开启" : "关闭"}
      </span>
    </div>
  );
}
