import {
  PanelSection,
  PanelSectionRow,
  ToggleField,
  Field,
  staticClasses,
} from "@decky/ui";
import {
  callable,
  definePlugin,
  addEventListener,
  removeEventListener,
} from "@decky/api";
import { useEffect, useState } from "react";
import { FaWifi } from "react-icons/fa";

interface Status {
  state: string;
  connected: boolean;
  active: boolean;
  busy: boolean;
  auto: boolean;
  adb_ok: boolean;
  quest_present: boolean;
  quest_status: string;
  model: string;
  rx_rate: number;
  tx_rate: number;
  rx_bytes: number;
  tx_bytes: number;
  uptime: number;
  reconnects: number;
  error: string;
}

const connectTether = callable<[], { ok: boolean; error: string }>("connect");
const disconnectTether = callable<[], { ok: boolean; error: string }>("disconnect");
const setAuto = callable<[enabled: boolean], { ok: boolean; auto: boolean }>("set_auto");
const getStatus = callable<[], Status>("get_status");

function fmtRate(b: number): string {
  if (b < 1024) return `${Math.round(b)} B/s`;
  if (b < 1048576) return `${(b / 1024).toFixed(1)} KB/s`;
  return `${(b / 1048576).toFixed(1)} MB/s`;
}

function fmtBytes(b: number): string {
  if (b < 1024) return `${Math.round(b)} B`;
  if (b < 1048576) return `${(b / 1024).toFixed(1)} KB`;
  if (b < 1073741824) return `${(b / 1048576).toFixed(1)} MB`;
  return `${(b / 1073741824).toFixed(2)} GB`;
}

function fmtUptime(s: number): string {
  const t = Math.floor(s);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(Math.floor(t / 3600))}:${p(Math.floor((t % 3600) / 60))}:${p(t % 60)}`;
}

const STATE_LABEL: Record<string, { text: string; color: string }> = {
  idle: { text: "Not Connected", color: "#9a9aae" },
  starting: { text: "Starting…", color: "#f0c929" },
  connected: { text: "Connected", color: "#00d97e" },
  reconnecting: { text: "Reconnecting…", color: "#f0c929" },
  stopping: { text: "Stopping…", color: "#f0c929" },
  error: { text: "Error", color: "#ff4757" },
};

function Content() {
  const [st, setSt] = useState<Status | null>(null);
  const [pending, setPending] = useState<boolean | null>(null); // optimistic tether target
  const [uiError, setUiError] = useState<string>("");

  useEffect(() => {
    getStatus().then(setSt).catch(() => {});
    const listener = addEventListener<[Status]>("tether_status", (s) => setSt(s));
    return () => removeEventListener("tether_status", listener);
  }, []);

  // Clear the optimistic target once the backend has caught up to it.
  useEffect(() => {
    if (pending !== null && st && !st.busy && st.active === pending) setPending(null);
  }, [st, pending]);

  const active = st?.active ?? false;
  const busy = st?.busy ?? false;
  const present = st?.quest_present ?? false;
  const unauthorized = st?.quest_status === "unauthorized";
  const stateInfo = STATE_LABEL[st?.state ?? "idle"] ?? STATE_LABEL.idle;
  const tetherOn = pending ?? active;

  const onToggleTether = async (value: boolean) => {
    setPending(value);
    setUiError("");
    try {
      const r = value ? await connectTether() : await disconnectTether();
      if (!r.ok) { setUiError(r.error || "failed"); setPending(null); }
    } catch (e) {
      setUiError(String(e));
      setPending(null);
    }
  };

  let hint = "";
  if (st && !st.adb_ok) hint = "adb unavailable — check setup";
  else if (!present) hint = "Plug in your Quest via USB";
  else if (unauthorized) hint = "Unauthorized — accept the prompt in the headset";

  const errText = uiError || (st && !active ? st.error : "");

  return (
    <PanelSection title="Quest Tether">
      <PanelSectionRow>
        <Field label="Status" focusable={false}>
          <span style={{ color: stateInfo.color, fontWeight: "bold" }}>{stateInfo.text}</span>
        </Field>
      </PanelSectionRow>

      <PanelSectionRow>
        <ToggleField
          label="Tether"
          description={hint || undefined}
          checked={tetherOn}
          disabled={busy || pending !== null || !present || unauthorized}
          onChange={onToggleTether}
        />
      </PanelSectionRow>

      <PanelSectionRow>
        <ToggleField
          label="Auto-connect"
          description="Tether automatically when the Quest is plugged in"
          checked={st?.auto ?? true}
          onChange={(v) => { void setAuto(v); }}
        />
      </PanelSectionRow>

      {active && st ? (
        <>
          <PanelSectionRow>
            <Field label="Down" focusable={false}>{fmtRate(st.rx_rate)}</Field>
          </PanelSectionRow>
          <PanelSectionRow>
            <Field label="Up" focusable={false}>{fmtRate(st.tx_rate)}</Field>
          </PanelSectionRow>
          <PanelSectionRow>
            <Field label="Uptime" focusable={false}>{fmtUptime(st.uptime)}</Field>
          </PanelSectionRow>
          <PanelSectionRow>
            <Field label="Total" focusable={false}>
              {`↓${fmtBytes(st.rx_bytes)}  ↑${fmtBytes(st.tx_bytes)}`}
            </Field>
          </PanelSectionRow>
          {st.model ? (
            <PanelSectionRow>
              <Field label="Device" focusable={false}>{st.model}</Field>
            </PanelSectionRow>
          ) : null}
          {st.reconnects > 0 ? (
            <PanelSectionRow>
              <Field label="Reconnects" focusable={false}>{String(st.reconnects)}</Field>
            </PanelSectionRow>
          ) : null}
        </>
      ) : null}

      {errText ? (
        <PanelSectionRow>
          <Field label="Error" focusable={false}>
            <span style={{ color: "#ff4757" }}>{errText}</span>
          </Field>
        </PanelSectionRow>
      ) : null}
    </PanelSection>
  );
}

export default definePlugin(() => ({
  name: "Quest Tether",
  titleView: <div className={staticClasses.Title}>Quest Tether</div>,
  content: <Content />,
  icon: <FaWifi />,
}));
