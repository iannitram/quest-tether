import {
  ButtonItem,
  Field,
  PanelSection,
  PanelSectionRow,
  staticClasses,
} from "@decky/ui";
import { callable, definePlugin } from "@decky/api";
import { useEffect, useState } from "react";
import { FaWifi } from "react-icons/fa";

interface Status {
  state: string;
  temp_c?: number | null;
  zone?: string;
  battery_level?: string;
  error?: string;
}

interface Action {
  ok: boolean;
  error?: string;
}

const getStatus = callable<[], Status>("status");
const connectTether = callable<[], Action>("connect");
const disconnectTether = callable<[], Action>("disconnect");

const POLL_MS = 3000;

const STATE_LABEL: Record<string, { text: string; color: string }> = {
  idle: { text: "Not connected", color: "#9a9aae" },
  starting: { text: "Starting…", color: "#f0c929" },
  connected: { text: "Connected", color: "#00d97e" },
  reconnecting: { text: "Reconnecting…", color: "#f0c929" },
  stopping: { text: "Stopping…", color: "#f0c929" },
  error: { text: "Error", color: "#ff4757" },
};

const ZONE_COLOR: Record<string, string> = {
  green: "#00d97e",
  yellow: "#f0c929",
  red: "#ff4757",
  unknown: "#9a9aae",
};

function temperatureText(temp: number | null | undefined): string {
  if (temp === null || temp === undefined || Number.isNaN(temp)) return "Unknown";
  return `${temp.toFixed(1)} °C`;
}

function isUp(state: string): boolean {
  return state === "connected" || state === "starting" || state === "reconnecting";
}

function Content() {
  const [st, setSt] = useState<Status | null>(null);
  const [pending, setPending] = useState(false);
  const [uiError, setUiError] = useState("");

  useEffect(() => {
    let alive = true;

    const tick = async () => {
      try {
        const next = await getStatus();
        if (alive) setSt(next);
      } catch {
        if (alive) setSt({ state: "service-down" });
      }
    };

    void tick();
    const id = window.setInterval(() => {
      void tick();
    }, POLL_MS);

    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  if (st === null) {
    return (
      <PanelSection title="Quest Tether">
        <PanelSectionRow>
          <Field label="Status" focusable={false}>
            Loading…
          </Field>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  if (st.state === "service-down") {
    return (
      <PanelSection title="Quest Tether">
        <PanelSectionRow>
          <Field label="Service" focusable={false}>
            Quest Tether service is not running.
          </Field>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  const stateInfo = STATE_LABEL[st.state] ?? STATE_LABEL.idle;
  const temp = st.temp_c ?? null;
  // The zone comes from qtetherd (tether_manager.temperature_zone), the one source of truth.
  const zone = st.zone ?? "unknown";
  const up = isUp(st.state);
  const errText = uiError || st.error || "";

  const act = async (connect: boolean) => {
    setPending(true);
    setUiError("");
    try {
      const result = connect ? await connectTether() : await disconnectTether();
      if (!result.ok) setUiError(result.error || "The command failed.");
    } catch (e) {
      setUiError(String(e));
    } finally {
      setPending(false);
      try {
        setSt(await getStatus());
      } catch {
        setSt({ state: "service-down" });
      }
    }
  };

  return (
    <PanelSection title="Quest Tether">
      <PanelSectionRow>
        <Field label="State" focusable={false}>
          <span style={{ color: stateInfo.color, fontWeight: "bold" }}>{stateInfo.text}</span>
        </Field>
      </PanelSectionRow>

      <PanelSectionRow>
        <Field label="Battery temperature" focusable={false}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: "0.5em" }}>
            <span
              aria-hidden="true"
              style={{
                display: "inline-block",
                width: "0.65em",
                height: "0.65em",
                borderRadius: "50%",
                backgroundColor: ZONE_COLOR[zone] ?? ZONE_COLOR.unknown,
              }}
            />
            <span>{temperatureText(temp)}</span>
          </span>
        </Field>
      </PanelSectionRow>

      <PanelSectionRow>
        <ButtonItem
          layout="below"
          onClick={() => {
            void act(!up);
          }}
          disabled={pending || st.state === "stopping"}
        >
          {up ? "Disconnect" : "Connect"}
        </ButtonItem>
      </PanelSectionRow>

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
