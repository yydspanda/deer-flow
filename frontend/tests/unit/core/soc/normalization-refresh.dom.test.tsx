import { afterEach, expect, test } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";

import {
  SocCorpusRunSettings,
  availableCorpusRunSettings,
  readCorpusRunSettings,
  RUN_SETTINGS_STORAGE_KEY,
} from "@/components/workspace/soc/soc-corpus-run-settings";
import type { SocAnalysisExecutionOptions } from "@/core/soc/types";

afterEach(() => {
  cleanup();
  window.sessionStorage.clear();
});

const defaults: SocAnalysisExecutionOptions = {
  normalization_review_mode: "apply",
  tenant_policy_enabled: false,
  tenant_policy_advisor_enabled: false,
  tenant_policy_signal_providers_enabled: false,
};
const controls = {
  defaults,
  normalization_review_available: true,
  tenant_policy_available: false,
  tenant_policy_advisor_available: false,
  tenant_policy_signal_providers_available: false,
};

test("fact refresh is explicit, defaults off and is disabled with semantic review", () => {
  function Form() {
    const [value, setValue] = useState(defaults);
    return (
      <SocCorpusRunSettings
        controls={controls}
        value={value}
        onChange={(next) =>
          setValue(availableCorpusRunSettings(next, controls))
        }
      />
    );
  }
  render(<Form />);
  const checkbox = screen.getByRole("checkbox", { name: "重新核对事实" });
  expect((checkbox as HTMLInputElement).checked).toBe(false);
  fireEvent.click(checkbox);
  expect((checkbox as HTMLInputElement).checked).toBe(true);
  fireEvent.click(screen.getByRole("switch", { name: "语义核对" }));
  expect(checkbox.hasAttribute("disabled")).toBe(true);
  expect((checkbox as HTMLInputElement).checked).toBe(false);
});

test("explicit refresh survives local selection but unavailable capability cannot enable it", () => {
  window.sessionStorage.setItem(
    RUN_SETTINGS_STORAGE_KEY,
    JSON.stringify({ ...defaults, refresh_normalization: true }),
  );
  expect(readCorpusRunSettings()?.refresh_normalization).toBe(true);
  expect(
    availableCorpusRunSettings(readCorpusRunSettings()!, {
      ...controls,
      normalization_review_available: false,
    }).refresh_normalization,
  ).toBeUndefined();
  window.sessionStorage.setItem(
    RUN_SETTINGS_STORAGE_KEY,
    JSON.stringify(defaults),
  );
  expect(readCorpusRunSettings()?.refresh_normalization).toBeUndefined();
});

test("batch settings do not inherit old single-alert policy preferences", () => {
  const batchKey = "soc.corpus.experiment.run-settings.v1";
  window.sessionStorage.setItem(
    RUN_SETTINGS_STORAGE_KEY,
    JSON.stringify({ ...defaults, tenant_policy_enabled: true }),
  );
  expect(readCorpusRunSettings(batchKey)).toBeNull();
  window.sessionStorage.setItem(batchKey, JSON.stringify(defaults));
  expect(readCorpusRunSettings(batchKey)).toEqual(defaults);
  expect(readCorpusRunSettings()?.tenant_policy_enabled).toBe(true);
  expect(
    availableCorpusRunSettings(readCorpusRunSettings()!, controls)
      .tenant_policy_enabled,
  ).toBe(false);
});
