const modeSelect = document.getElementById("mode");
const spliceBanner = document.getElementById("splice-banner");
const transitionBanner = document.getElementById("transition-banner");
const sourceDropzone = document.getElementById("source-dropzone");
const referenceDropzone = document.getElementById("reference-dropzone");
const sourceDropTitle = document.getElementById("source-drop-title");
const sourceDropHint = document.getElementById("source-drop-hint");
const referenceDropHint = document.getElementById("reference-drop-hint");
const referenceDropTitle = document.getElementById("reference-drop-title");
const swapClipsButton = document.getElementById("swap-clips");
const sourceInput = document.getElementById("source-input");
const referenceInput = document.getElementById("reference-input");
const sourceName = document.getElementById("source-name");
const referenceName = document.getElementById("reference-name");
const sourcePreview = document.getElementById("source-preview");
const referencePreview = document.getElementById("reference-preview");
const sourcePreviewLabel = document.getElementById("source-preview-label");
const referencePreviewLabel = document.getElementById("reference-preview-label");
const anchorPreviewCard = document.getElementById("anchor-preview-card");
const previewGrid = document.getElementById("preview-grid");
const outputPreview = document.getElementById("output-preview");
const downloadLink = document.getElementById("download-link");
const moshButton = document.getElementById("mosh-button");
const statusEl = document.getElementById("status");
const statsEl = document.getElementById("stats");
const progressPanel = document.getElementById("progress-panel");
const progressFill = document.getElementById("progress-fill");
const progressPercent = document.getElementById("progress-percent");
const progressStage = document.getElementById("progress-stage");
const tooltipEl = document.getElementById("tooltip");

const gopInput = document.getElementById("gop");
const gopValue = document.getElementById("gop-value");
const moshStartInput = document.getElementById("mosh-start");
const moshStartLabel = document.getElementById("mosh-start-label");
const moshStartValue = document.getElementById("mosh-start-value");
const moshEndInput = document.getElementById("mosh-end");
const moshEndValue = document.getElementById("mosh-end-value");
const moshEndField = document.getElementById("mosh-end-field");
const transitionDurationInput = document.getElementById("transition-duration");
const transitionDurationValue = document.getElementById("transition-duration-value");
const transitionDurationField = document.getElementById("transition-duration-field");
const duplicateInput = document.getElementById("duplicate-copies");
const duplicateValue = document.getElementById("duplicate-value");
const probabilityInput = document.getElementById("duplicate-probability");
const probabilityValue = document.getElementById("probability-value");
const presetSelect = document.getElementById("preset-select");
const presetDescription = document.getElementById("preset-description");

let sourceObjectUrl = null;
let referenceObjectUrl = null;
let tooltipConfig = {};
let pollTimer = null;
let loadedPresets = [];
let activePresetId = null;

async function readJsonResponse(response) {
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    const text = await response.text();
    const snippet = text.replace(/\s+/g, " ").trim().slice(0, 120);
    throw new Error(
      `Server returned non-JSON (${response.status}). Restart ./run_ui.sh. ${snippet}`,
    );
  }
  return response.json();
}

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("error", isError);
}

function setProgress(percent, stage) {
  progressPanel.classList.remove("hidden");
  progressFill.style.width = `${percent}%`;
  progressPercent.textContent = `${percent}%`;
  progressStage.textContent = stage;
}

function resetProgress() {
  progressFill.style.width = "0%";
  progressPercent.textContent = "0%";
  progressStage.textContent = "Starting…";
}

function hideProgress() {
  progressPanel.classList.add("hidden");
}

function bindSlider(input, label, formatter) {
  const update = () => {
    label.textContent = formatter(input.value);
  };
  input.addEventListener("input", update);
  update();
}

bindSlider(gopInput, gopValue, (value) => value);
bindSlider(duplicateInput, duplicateValue, (value) => value);
bindSlider(probabilityInput, probabilityValue, (value) => `${value}%`);

function formatMoshEndLabel(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return "end of clip";
  }
  return `${numeric.toFixed(1)}s`;
}

bindSlider(moshStartInput, moshStartValue, (value) => `${Number(value).toFixed(1)}s`);
bindSlider(moshEndInput, moshEndValue, formatMoshEndLabel);
bindSlider(transitionDurationInput, transitionDurationValue, (value) => `${Number(value).toFixed(1)}s`);

function syncMoshWindowSliders() {
  const duration = Number(moshEndInput.max);
  const start = Number(moshStartInput.value);
  const end = Number(moshEndInput.value);
  if (Number.isFinite(duration) && duration > 0 && end > 0 && end <= start) {
    moshEndInput.value = String(Math.min(duration, start + 0.1));
    moshEndInput.dispatchEvent(new Event("input"));
  }
}

moshStartInput.addEventListener("input", () => {
  syncMoshWindowSliders();
  if (modeSelect.value === "transition") {
    syncTransitionSliders();
  }
});
moshEndInput.addEventListener("input", syncMoshWindowSliders);

function isDualClipMode() {
  return modeSelect.value === "two" || modeSelect.value === "transition";
}

function toggleReferenceMode() {
  const mode = modeSelect.value;
  const twoClip = mode === "two";
  const transition = mode === "transition";
  const dualClip = isDualClipMode();

  referenceDropzone.classList.toggle("hidden", !dualClip);
  spliceBanner.classList.toggle("hidden", !twoClip);
  transitionBanner.classList.toggle("hidden", !transition);
  swapClipsButton.classList.toggle("hidden", !dualClip);
  anchorPreviewCard.classList.toggle("hidden", !twoClip);
  previewGrid.classList.toggle("preview-grid-three", dualClip);
  moshEndField.classList.toggle("hidden", transition);
  transitionDurationField.classList.toggle("hidden", !transition);

  if (transition) {
    sourceDropTitle.textContent = "Video 1";
    sourceDropHint.textContent = "Plays clean until the transition point";
    referenceDropTitle.textContent = "Video 2";
    referenceDropHint.textContent =
      "Motion source for the bridge, then plays clean";
    sourcePreviewLabel.textContent = "Video 1";
    referencePreviewLabel.textContent = "Video 2";
    moshStartLabel.textContent = "Transition start";
  } else if (twoClip) {
    sourceDropTitle.textContent = "Motion clip";
    sourceDropHint.textContent = "P-frames spliced onto the anchor — motion and color bleed through";
    referenceDropTitle.textContent = "Anchor clip";
    referenceDropHint.textContent =
      "Still image / decode anchor — first I-frame is kept";
    sourcePreviewLabel.textContent = "Motion";
    referencePreviewLabel.textContent = "Anchor";
    moshStartLabel.textContent = "Mosh start";
  } else {
    sourceDropTitle.textContent = "Source video";
    sourceDropHint.textContent = "Drop a clip or click to browse";
    sourcePreviewLabel.textContent = "Source";
    moshStartLabel.textContent = "Mosh start";
  }
}

modeSelect.addEventListener("change", () => {
  toggleReferenceMode();
  syncTransitionSliders();
});
toggleReferenceMode();

transitionDurationInput.addEventListener("input", syncTransitionSliders);

function revokeReferencePreview() {
  if (referenceObjectUrl) {
    URL.revokeObjectURL(referenceObjectUrl);
    referenceObjectUrl = null;
  }
  referencePreview.removeAttribute("src");
  referencePreview.load();
}

function wireDropzone(zone, input, onFile) {
  zone.addEventListener("click", (event) => {
    if (event.target.closest(".tooltip-trigger")) {
      return;
    }
    input.click();
  });

  zone.addEventListener("dragover", (event) => {
    event.preventDefault();
    zone.classList.add("dragover");
  });

  zone.addEventListener("dragleave", () => {
    zone.classList.remove("dragover");
  });

  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    zone.classList.remove("dragover");
    const file = event.dataTransfer?.files?.[0];
    if (file) {
      onFile(file);
    }
  });

  input.addEventListener("change", () => {
    const file = input.files?.[0];
    if (file) {
      onFile(file);
    }
  });
}

const MIN_TRANSITION_START_MARGIN = 0.1;
const MIN_TRANSITION_SUFFIX = 0.5;
const MIN_TRANSITION_LENGTH = 0.5;

function video1Duration() {
  const duration = sourcePreview.duration;
  return Number.isFinite(duration) && duration > 0 ? duration : null;
}

function video2Duration() {
  const duration = referencePreview.duration;
  return Number.isFinite(duration) && duration > 0 ? duration : null;
}

function defaultTransitionStart(duration) {
  const transitionLen = Number(transitionDurationInput.value) || MIN_TRANSITION_LENGTH;
  const maxStart = Math.max(duration - MIN_TRANSITION_START_MARGIN, MIN_TRANSITION_START_MARGIN);
  const start = Math.max(duration * 0.7, duration - transitionLen - MIN_TRANSITION_SUFFIX);
  return Math.min(Math.max(start, MIN_TRANSITION_START_MARGIN), maxStart);
}

function syncTransitionSliders() {
  if (modeSelect.value !== "transition") {
    return;
  }

  const v1 = video1Duration();
  const v2 = video2Duration();

  if (v1 === null) {
    moshStartInput.disabled = true;
  } else {
    moshStartInput.disabled = false;
    const maxStart = Math.max(v1 - MIN_TRANSITION_START_MARGIN, MIN_TRANSITION_START_MARGIN);
    moshStartInput.max = maxStart.toFixed(1);
    if (Number(moshStartInput.value) > maxStart) {
      moshStartInput.value = maxStart.toFixed(1);
      moshStartInput.dispatchEvent(new Event("input"));
    }
  }

  if (v2 === null) {
    transitionDurationInput.disabled = true;
  } else {
    transitionDurationInput.disabled = false;
    const maxLen = Math.max(v2 - MIN_TRANSITION_SUFFIX, MIN_TRANSITION_LENGTH);
    transitionDurationInput.max = maxLen.toFixed(1);
    transitionDurationInput.min = String(Math.min(MIN_TRANSITION_LENGTH, maxLen));
    if (Number(transitionDurationInput.value) > maxLen) {
      transitionDurationInput.value = maxLen.toFixed(1);
      transitionDurationInput.dispatchEvent(new Event("input"));
    }
    if (Number(transitionDurationInput.value) < Number(transitionDurationInput.min)) {
      transitionDurationInput.value = transitionDurationInput.min;
      transitionDurationInput.dispatchEvent(new Event("input"));
    }
  }
}

function validateTransitionInputs() {
  const v1 = video1Duration();
  const v2 = video2Duration();
  if (v1 === null) {
    return "Load video 1 to set the transition start.";
  }
  if (v2 === null) {
    return "Load video 2 to set the transition length.";
  }

  const start = Number(moshStartInput.value);
  const length = Number(transitionDurationInput.value);
  const maxStart = v1 - MIN_TRANSITION_START_MARGIN;
  const maxLen = v2 - MIN_TRANSITION_SUFFIX;

  if (!Number.isFinite(start) || start <= 0) {
    return "Set a transition start point on video 1.";
  }
  if (start > maxStart) {
    return `Transition start must be at least ${MIN_TRANSITION_START_MARGIN.toFixed(1)}s before the end of video 1 (max ${maxStart.toFixed(1)}s).`;
  }
  if (!Number.isFinite(length) || length < MIN_TRANSITION_LENGTH) {
    return `Transition length must be at least ${MIN_TRANSITION_LENGTH.toFixed(1)}s.`;
  }
  if (length > maxLen) {
    return `Transition length must leave ${MIN_TRANSITION_SUFFIX.toFixed(1)}s of video 2 for the clean ending (max ${maxLen.toFixed(1)}s).`;
  }
  return null;
}

function setSourceFile(file) {
  sourceInput.files = createFileList(file);
  sourceName.textContent = file.name;
  if (sourceObjectUrl) {
    URL.revokeObjectURL(sourceObjectUrl);
  }
  sourceObjectUrl = URL.createObjectURL(file);
  sourcePreview.src = sourceObjectUrl;
  sourcePreview.onloadedmetadata = () => {
    const duration = sourcePreview.duration;
    if (!Number.isFinite(duration) || duration <= 0) {
      moshStartInput.disabled = true;
      moshEndInput.disabled = true;
      return;
    }
    const durationLabel = duration.toFixed(1);
    moshStartInput.disabled = false;
    moshEndInput.disabled = false;
    moshStartInput.max = durationLabel;
    moshEndInput.max = durationLabel;
    if (modeSelect.value === "transition") {
      syncTransitionSliders();
      const defaultStart = defaultTransitionStart(duration);
      moshStartInput.value = defaultStart.toFixed(1);
      moshStartInput.dispatchEvent(new Event("input"));
    } else if (Number(moshStartInput.value) > duration) {
      moshStartInput.value = "0";
      moshStartValue.textContent = "0.0s";
    }
    if (Number(moshEndInput.value) > duration) {
      moshEndInput.value = "0";
      moshEndValue.textContent = "end of clip";
    }
    syncMoshWindowSliders();
  };
}

function setReferenceFile(file) {
  referenceInput.files = createFileList(file);
  referenceName.textContent = file.name;
  revokeReferencePreview();
  referenceObjectUrl = URL.createObjectURL(file);
  referencePreview.src = referenceObjectUrl;
  referencePreview.onloadedmetadata = () => {
    syncTransitionSliders();
  };
}

function swapClips() {
  const sourceFile = sourceInput.files?.[0];
  const referenceFile = referenceInput.files?.[0];
  if (!sourceFile && !referenceFile) {
    return;
  }

  if (sourceFile) {
    setReferenceFile(sourceFile);
  } else {
    referenceInput.files = new DataTransfer().files;
    referenceName.textContent = "No file selected";
    revokeReferencePreview();
  }

  if (referenceFile) {
    setSourceFile(referenceFile);
  } else {
    sourceInput.files = new DataTransfer().files;
    sourceName.textContent = "No file selected";
    if (sourceObjectUrl) {
      URL.revokeObjectURL(sourceObjectUrl);
      sourceObjectUrl = null;
    }
    sourcePreview.removeAttribute("src");
    sourcePreview.load();
    moshStartInput.disabled = true;
    moshEndInput.disabled = true;
  }

  setStatus("Swapped anchor and motion clips.");
}

swapClipsButton.addEventListener("click", swapClips);

function createFileList(file) {
  const transfer = new DataTransfer();
  transfer.items.add(file);
  return transfer.files;
}

wireDropzone(sourceDropzone, sourceInput, setSourceFile);
wireDropzone(referenceDropzone, referenceInput, setReferenceFile);

function renderTooltipContent(entry) {
  if (!entry) {
    return "<p>No tooltip configured for this control.</p>";
  }
  return `
    <h4>${escapeHtml(entry.title || "Control")}</h4>
    <p class="tooltip-label">What it is</p>
    <p>${escapeHtml(entry.summary || "")}</p>
    <p class="tooltip-label">How it changes the video</p>
    <p>${escapeHtml(entry.effect || "")}</p>
  `;
}

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function positionTooltip(trigger) {
  const rect = trigger.getBoundingClientRect();
  const margin = 12;
  tooltipEl.style.left = "0px";
  tooltipEl.style.top = "0px";
  tooltipEl.classList.remove("hidden");

  const tooltipRect = tooltipEl.getBoundingClientRect();
  let left = rect.left + rect.width / 2 - tooltipRect.width / 2;
  let top = rect.bottom + margin;

  if (left + tooltipRect.width > window.innerWidth - margin) {
    left = window.innerWidth - tooltipRect.width - margin;
  }
  if (left < margin) {
    left = margin;
  }
  if (top + tooltipRect.height > window.innerHeight - margin) {
    top = rect.top - tooltipRect.height - margin;
  }

  tooltipEl.style.left = `${left}px`;
  tooltipEl.style.top = `${top}px`;
}

function hideTooltip() {
  tooltipEl.classList.add("hidden");
}

function showTooltip(trigger) {
  const tooltipId = trigger.dataset.tooltipId;
  const entry = tooltipConfig[tooltipId];
  tooltipEl.innerHTML = renderTooltipContent(entry);
  positionTooltip(trigger);
}

function initTooltips() {
  document.querySelectorAll(".tooltip-trigger").forEach((trigger) => {
    trigger.addEventListener("mouseenter", () => showTooltip(trigger));
    trigger.addEventListener("focus", () => showTooltip(trigger));
    trigger.addEventListener("mouseleave", hideTooltip);
    trigger.addEventListener("blur", hideTooltip);
  });

  window.addEventListener(
    "scroll",
    () => {
      hideTooltip();
    },
    true,
  );
}

async function loadTooltips() {
  try {
    const response = await fetch("/api/tooltips");
    if (!response.ok) {
      return;
    }
    tooltipConfig = await readJsonResponse(response);
    initTooltips();
  } catch {
    initTooltips();
  }
}

function applyPreset(preset) {
  const presetMode = preset.mode || "single";
  if (presetMode === "two" || presetMode === "transition") {
    modeSelect.value = presetMode;
  } else if (!isDualClipMode()) {
    modeSelect.value = presetMode;
  }
  toggleReferenceMode();

  gopInput.value = String(preset.gop ?? 250);
  gopInput.dispatchEvent(new Event("input"));

  const moshStart = preset.mosh_start_seconds ?? 0;
  moshStartInput.value = String(moshStart);
  if (!moshStartInput.disabled) {
    const max = Number(moshStartInput.max);
    if (Number.isFinite(max) && moshStart > max) {
      moshStartInput.value = String(max);
    }
  }
  moshStartInput.dispatchEvent(new Event("input"));

  const moshEnd = preset.mosh_end_seconds ?? 0;
  moshEndInput.value = String(moshEnd);
  if (!moshEndInput.disabled) {
    const max = Number(moshEndInput.max);
    if (Number.isFinite(max) && moshEnd > max) {
      moshEndInput.value = "0";
    }
  }
  moshEndInput.dispatchEvent(new Event("input"));
  syncMoshWindowSliders();

  const transitionDuration = preset.transition_duration_seconds ?? 2;
  transitionDurationInput.value = String(transitionDuration);
  transitionDurationInput.dispatchEvent(new Event("input"));
  syncTransitionSliders();

  duplicateInput.value = String(preset.duplicate_copies ?? 0);
  duplicateInput.dispatchEvent(new Event("input"));

  probabilityInput.value = String(Math.round((preset.duplicate_probability ?? 1) * 100));
  probabilityInput.dispatchEvent(new Event("input"));

  document.getElementById("keep-first-idr").value = String(preset.keep_first_idr ?? 1);
  document.getElementById("width").value = preset.width != null ? String(preset.width) : "";
  document.getElementById("crf").value = String(preset.crf ?? 18);
  document.getElementById("seed").value = preset.seed != null ? String(preset.seed) : "";
  document.getElementById("remove-sps-pps").checked = Boolean(preset.remove_sps_pps);
  document.getElementById("no-prep").checked = Boolean(preset.no_prep);
  document.getElementById("no-audio").checked = Boolean(preset.no_audio);

  activePresetId = preset.id;
  presetSelect.value = preset.id;
  updatePresetDescription();

  const keptDualMode =
    isDualClipMode() && presetMode !== "two" && presetMode !== "transition";
  setStatus(
    keptDualMode
      ? `Preset applied: ${preset.name} (${modeSelect.value === "transition" ? "transition" : "splice"} mode kept)`
      : `Preset applied: ${preset.name}`,
  );
}

function updatePresetDescription() {
  const preset = loadedPresets.find((item) => item.id === presetSelect.value);
  presetDescription.textContent = preset?.description || "";
}

function renderPresets(presets) {
  loadedPresets = presets;
  presetSelect.innerHTML = '<option value="">Choose a preset…</option>';
  presets.forEach((preset) => {
    const option = document.createElement("option");
    option.value = preset.id;
    option.textContent = preset.name;
    presetSelect.appendChild(option);
  });
  if (activePresetId) {
    presetSelect.value = activePresetId;
  }
  updatePresetDescription();
}

presetSelect.addEventListener("change", () => {
  const preset = loadedPresets.find((item) => item.id === presetSelect.value);
  if (preset) {
    applyPreset(preset);
    return;
  }
  activePresetId = null;
  updatePresetDescription();
});

async function loadPresets() {
  try {
    const response = await fetch("/api/presets");
    if (!response.ok) {
      return;
    }
    const payload = await readJsonResponse(response);
    renderPresets(payload.presets || []);
  } catch {
    // Presets are optional in the UI.
  }
}

function sleep(ms) {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

async function pollJob(jobId) {
  while (true) {
    const response = await fetch(`/api/jobs/${jobId}`);
    const payload = await readJsonResponse(response);
    if (!response.ok) {
      throw new Error(payload.error || "Could not read job status.");
    }

    setProgress(payload.progress ?? 0, payload.stage ?? "Working…");

    if (payload.status === "complete" && payload.result) {
      return payload.result;
    }
    if (payload.status === "error") {
      throw new Error(payload.error || "Datamosh failed.");
    }

    await sleep(300);
  }
}

function formatMoshWindowStat(result) {
  if (result.mode === "transition") {
    const start =
      result.mosh_start_seconds != null
        ? `${Number(result.mosh_start_seconds).toFixed(1)}s`
        : "auto";
    const duration =
      result.transition_duration_seconds != null
        ? `${Number(result.transition_duration_seconds).toFixed(1)}s`
        : "2.0s";
    return `${start} + ${duration} bridge → video 2`;
  }
  const start =
    result.mosh_start_seconds != null
      ? `${Number(result.mosh_start_seconds).toFixed(1)}s`
      : "0s";
  const end =
    result.mosh_end_seconds != null
      ? `${Number(result.mosh_end_seconds).toFixed(1)}s`
      : "end";
  const startFrame =
    result.mosh_start_vcl_index != null ? ` f${result.mosh_start_vcl_index}` : "";
  const endFrame = result.mosh_end_vcl_index != null ? ` f${result.mosh_end_vcl_index}` : "";
  return `${start}${startFrame} → ${end}${endFrame}`;
}

function applyResult(result) {
  outputPreview.src = `${result.output_url}?t=${Date.now()}`;
  downloadLink.href = result.output_url;
  downloadLink.download = result.filename;
  downloadLink.classList.remove("hidden");

  document.getElementById("stat-mode").textContent = result.mode;
  document.getElementById("stat-mosh-window").textContent = formatMoshWindowStat(result);
  document.getElementById("stat-idr").textContent = `${result.idr_before} → ${result.idr_after}`;
  document.getElementById("stat-output").textContent = result.filename;
  statsEl.classList.remove("hidden");

  if (result.idr_auto_kept) {
    return "Done. Kept 1 IDR frame automatically so the MP4 can play.";
  }
  return "Done. Preview the output or download the MP4.";
}

moshButton.addEventListener("click", async () => {
  const sourceFile = sourceInput.files?.[0];
  if (!sourceFile) {
    setStatus("Select a source video first.", true);
    return;
  }

  const dualClip = isDualClipMode();
  const referenceFile = referenceInput.files?.[0];
  if (dualClip && !referenceFile) {
    const label = modeSelect.value === "transition" ? "Video 2" : "Anchor/motion clip pair";
    setStatus(`${label} is required for this mode.`, true);
    return;
  }

  const transition = modeSelect.value === "transition";
  const moshEnd = Number(moshEndInput.value);
  const moshStart = Number(moshStartInput.value);
  if (!transition && moshEnd > 0 && moshEnd <= moshStart) {
    setStatus("Mosh end must be after mosh start.", true);
    return;
  }

  if (transition) {
    const transitionError = validateTransitionInputs();
    if (transitionError) {
      setStatus(transitionError, true);
      return;
    }
  }

  const formData = new FormData();
  formData.append("source", sourceFile);
  if (referenceFile) {
    formData.append("reference", referenceFile);
  }

  formData.append("mode", modeSelect.value);
  formData.append("mosh_start_seconds", moshStartInput.value);
  if (!transition && Number(moshEndInput.value) > 0) {
    formData.append("mosh_end_seconds", moshEndInput.value);
  }
  if (transition) {
    formData.append("transition_duration_seconds", transitionDurationInput.value);
  }
  formData.append("gop", gopInput.value);
  formData.append("duplicate_copies", duplicateInput.value);
  formData.append("duplicate_probability", (Number(probabilityInput.value) / 100).toString());
  formData.append("keep_first_idr", document.getElementById("keep-first-idr").value);
  formData.append("crf", document.getElementById("crf").value);
  formData.append("remove_sps_pps", document.getElementById("remove-sps-pps").checked ? "1" : "0");
  formData.append("no_prep", document.getElementById("no-prep").checked ? "1" : "0");
  formData.append("no_audio", document.getElementById("no-audio").checked ? "1" : "0");

  const width = document.getElementById("width").value;
  if (width) {
    formData.append("width", width);
  }

  const seed = document.getElementById("seed").value;
  if (seed) {
    formData.append("seed", seed);
  }

  moshButton.disabled = true;
  resetProgress();
  setProgress(0, "Uploading and starting job…");
  setStatus("");
  statsEl.classList.add("hidden");
  downloadLink.classList.add("hidden");

  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }

  try {
    const response = await fetch("/api/mosh", {
      method: "POST",
      body: formData,
    });
    const payload = await readJsonResponse(response);
    if (!response.ok) {
      throw new Error(payload.error || "Datamosh failed.");
    }

    const result = await pollJob(payload.job_id);
    setProgress(100, "Complete");
    setStatus(applyResult(result));
  } catch (error) {
    hideProgress();
    setStatus(error.message || "Unexpected error.", true);
  } finally {
    moshButton.disabled = false;
  }
});

loadTooltips();
loadPresets();
