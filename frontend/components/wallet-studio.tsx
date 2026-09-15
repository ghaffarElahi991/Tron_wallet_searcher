"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  api,
  type GenerationJob,
  type GpuFleet,
  type JobStatus,
  type PatternType,
  type User,
} from "@/lib/api";
import { encryptedWalletFile, verifyServerWallet } from "@/lib/wallet-crypto";
import { AuthScreen } from "./auth-screen";
import {
  ActivityIcon,
  AlertIcon,
  ArrowIcon,
  CheckIcon,
  ChevronIcon,
  ClockIcon,
  CopyIcon,
  DownloadIcon,
  ExternalIcon,
  GridIcon,
  HelpIcon,
  KeyIcon,
  LockIcon,
  OrdersIcon,
  ShieldIcon,
  SparkIcon,
  TelegramIcon,
  WalletIcon,
} from "./icons";

type View = "studio" | "orders" | "security" | "help";
type Flow = "configure" | "generating" | "ready" | "delivery" | "funding" | "confirmed";

const TRON_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
const VALID_FIRST_CUSTOM_CHARACTER = /^[9A-HJ-NP-Z]$/;
const TERMINAL_FAILURES: JobStatus[] = ["failed", "canceled", "timed_out"];

const navItems: Array<{ id: View; label: string; icon: typeof GridIcon }> = [
  { id: "studio", label: "Wallet studio", icon: GridIcon },
  { id: "orders", label: "My orders", icon: OrdersIcon },
  { id: "security", label: "Security", icon: ShieldIcon },
  { id: "help", label: "Help center", icon: HelpIcon },
];

const presets: Array<{ id: PatternType; label: string; prefix: string; suffix: string; prefixLength: number; suffixLength: number; detail: string }> = [
  { id: "3x4", label: "3 × 4", prefix: "TABC", suffix: "7777", prefixLength: 3, suffixLength: 4, detail: "T + prefix 3 · suffix 4" },
  { id: "2x5", label: "2 × 5", prefix: "TXR", suffix: "88888", prefixLength: 2, suffixLength: 5, detail: "T + prefix 2 · suffix 5" },
  { id: "4x3", label: "4 × 3", prefix: "TRXQ9", suffix: "999", prefixLength: 4, suffixLength: 3, detail: "T + prefix 4 · suffix 3" },
  { id: "2x2", label: "2 × 2", prefix: "TQR", suffix: "99", prefixLength: 2, suffixLength: 2, detail: "T + prefix 2 · suffix 2" },
];

function formatRate(value: number) {
  if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)}B/s`;
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M/s`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K/s`;
  return `${value}/s`;
}

function formatAttempts(value: number) {
  if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)}B`;
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return value.toLocaleString();
}

function displayError(error: unknown) {
  return error instanceof ApiError || error instanceof Error
    ? error.message
    : "The request could not be completed.";
}

function getDifficulty(prefix: string, suffix: string) {
  const effective = Math.max(0, prefix.length - 1) + suffix.length;
  const alphaCount = `${prefix.slice(1)}${suffix}`.replace(/[^a-z]/gi, "").length;
  const score = Math.max(1, effective * 0.72 - alphaCount * 0.12);
  if (score <= 4) return { label: "Easy", wait: "2–8 sec", p95: "under 25 sec", level: 32, tone: "easy" };
  if (score <= 5.3) return { label: "Balanced", wait: "8–24 sec", p95: "under 70 sec", level: 56, tone: "balanced" };
  if (score <= 6.5) return { label: "Advanced", wait: "1–4 min", p95: "under 12 min", level: 76, tone: "advanced" };
  return { label: "Extreme", wait: "30+ min", p95: "varies widely", level: 94, tone: "extreme" };
}

function shortAddress(address: string) {
  return `${address.slice(0, 8)}••••••••••••••••${address.slice(-6)}`;
}

function Sidebar({ view, setView, user, orderCount, onLogout }: { view: View; setView: (view: View) => void; user: User; orderCount: number; onLogout: () => void }) {
  const initials = user.username.slice(0, 2).toUpperCase();
  return (
    <aside className="sidebar">
      <div className="brand" role="banner">
        <span className="brand-mark"><span>TF</span></span>
        <span className="brand-name">Tron<span>Forge</span></span>
      </div>

      <nav className="main-nav" aria-label="Primary navigation">
        <p className="nav-eyebrow">Workspace</p>
        {navItems.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.id}
              className={`nav-item ${view === item.id ? "active" : ""}`}
              onClick={() => setView(item.id)}
            >
              <Icon />
              <span>{item.label}</span>
              {item.id === "orders" && orderCount > 0 && <span className="nav-count">{orderCount}</span>}
            </button>
          );
        })}
      </nav>

      <div className="telegram-card">
        <span className="telegram-badge api-badge"><ActivityIcon /></span>
        <div>
          <strong>API connected</strong>
          <p>Jobs and GPU state are synchronized with the backend.</p>
        </div>
        <span className="connected-dot" aria-label="Connected" />
      </div>

      <div className="sidebar-profile">
        <span className="avatar">{initials}</span>
        <div>
          <strong>{user.username}</strong>
          <span>Authenticated</span>
        </div>
        <button className="icon-button logout-button" aria-label="Sign out" title="Sign out" onClick={onLogout}>↪</button>
      </div>
    </aside>
  );
}

function Header({ view, fleet }: { view: View; fleet: GpuFleet | null }) {
  const titles: Record<View, { eyebrow: string; title: string }> = {
    studio: { eyebrow: "Vanity wallet workspace", title: "Create a wallet" },
    orders: { eyebrow: "Order activity", title: "My orders" },
    security: { eyebrow: "Trust center", title: "Security model" },
    help: { eyebrow: "Guides and answers", title: "Help center" },
  };
  return (
    <header className="topbar">
      <div>
        <p>{titles[view].eyebrow}</p>
        <h1>{titles[view].title}</h1>
      </div>
      <div className="topbar-actions">
        <span className={`environment-pill ${fleet ? "online" : ""}`}><span /> {fleet ? `${fleet.total} ${fleet.mode === "simulator" ? "simulated" : "GPU"} workers` : "API reconnecting"}</span>
        <button className="notification-button" aria-label="Notifications">
          <span className="bell-shape" />
          <i />
        </button>
      </div>
    </header>
  );
}

function StepRail({ flow }: { flow: Flow }) {
  const activeIndex = flow === "configure" ? 0 : flow === "generating" ? 1 : flow === "ready" || flow === "delivery" ? 2 : 3;
  const steps = ["Pattern", "Generate", "Secure", "Fund"];
  return (
    <div className="step-rail" aria-label="Wallet creation progress">
      {steps.map((step, index) => (
        <div className={`step ${index === activeIndex ? "active" : ""} ${index < activeIndex ? "done" : ""}`} key={step}>
          <span>{index < activeIndex ? <CheckIcon /> : index + 1}</span>
          <strong>{step}</strong>
          {index < steps.length - 1 && <i />}
        </div>
      ))}
    </div>
  );
}

function AddressPreview({ prefix, suffix }: { prefix: string; suffix: string }) {
  const middleLength = Math.max(5, 34 - prefix.length - suffix.length);
  return (
    <div className="address-preview">
      <div className="preview-topline">
        <span>Live address preview</span>
        <span><i /> TRON Mainnet format</span>
      </div>
      <div className="address-string" aria-label="Address pattern preview">
        <mark className="fixed-network-prefix">T</mark>
        {prefix.slice(1) && <mark>{prefix.slice(1)}</mark>}
        <span>{"•".repeat(middleLength)}</span>
        {suffix && <mark>{suffix}</mark>}
      </div>
      <p>The dots represent characters discovered by the GPU search.</p>
    </div>
  );
}

function ConfigureView({ onStart, fleet, externalError }: { onStart: (data: { pattern: PatternType; prefix: string; suffix: string }) => Promise<void>; fleet: GpuFleet | null; externalError: string }) {
  const [prefix, setPrefix] = useState("TABC");
  const [suffix, setSuffix] = useState("7777");
  const [selectedPreset, setSelectedPreset] = useState(0);
  const [error, setError] = useState("");
  const [characterError, setCharacterError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const selectedPattern = presets[selectedPreset];
  const difficulty = useMemo(() => getDifficulty(prefix, suffix), [prefix, suffix]);

  function filterTronCharacters(value: string, field: "prefix" | "suffix") {
    const invalidCharacters = Array.from(new Set([...value].filter((character) => !TRON_BASE58_ALPHABET.includes(character))));
    if (invalidCharacters.length > 0) {
      const displayed = invalidCharacters.map((character) => character === " " ? "space" : `“${character}”`).join(", ");
      setCharacterError(`${displayed} ${invalidCharacters.length === 1 ? "is not a valid" : "are not valid"} TRON ${field} ${invalidCharacters.length === 1 ? "character" : "characters"}. TRON Base58 excludes 0, O, I, and lowercase l.`);
    } else {
      setCharacterError("");
    }
    return [...value].filter((character) => TRON_BASE58_ALPHABET.includes(character)).join("");
  }

  function updatePrefix(value: string) {
    const clean = filterTronCharacters(value, "prefix");
    if (clean[0] && !VALID_FIRST_CUSTOM_CHARACTER.test(clean[0])) {
      setCharacterError(`“${clean[0]}” is not allowed directly after T. The first character you enter must be an uppercase Base58 letter or 9.`);
      return;
    }
    setPrefix(`T${clean.slice(0, selectedPattern.prefixLength)}`);
    setError("");
  }

  function updateSuffix(value: string) {
    setSuffix(filterTronCharacters(value, "suffix").slice(0, selectedPattern.suffixLength));
    setError("");
  }

  function choosePreset(index: number) {
    const preset = presets[index];
    setSelectedPreset(index);
    setPrefix(preset.prefix);
    setSuffix(preset.suffix);
    setError("");
    setCharacterError("");
  }

  async function submit() {
    if (!VALID_FIRST_CUSTOM_CHARACTER.test(prefix.slice(1, 2))) {
      setError("The first character after T must be an uppercase Base58 letter or 9.");
      return;
    }
    if (prefix.length !== selectedPattern.prefixLength + 1 || suffix.length !== selectedPattern.suffixLength) {
      setError(`The ${selectedPattern.label} pattern requires exactly ${selectedPattern.prefixLength} prefix characters after T and ${selectedPattern.suffixLength} suffix characters.`);
      return;
    }
    setSubmitting(true);
    try {
      await onStart({ pattern: selectedPattern.id, prefix, suffix });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="studio-grid">
      <section className="builder-card panel">
        <div className="panel-heading">
          <div>
            <span className="section-kicker">01 — Choose your pattern</span>
            <h2>Make it unmistakably yours.</h2>
            <p>Choose one of the four supported TRON address patterns.</p>
          </div>
          <span className="shield-chip"><ShieldIcon /> Safe rules only</span>
        </div>

        <div className="preset-grid">
          {presets.map((preset, index) => (
            <button
              className={`preset ${selectedPreset === index ? "selected" : ""}`}
              key={preset.label}
              onClick={() => choosePreset(index)}
            >
              <span className="preset-check">{selectedPreset === index && <CheckIcon />}</span>
              <strong>{preset.label}</strong>
              <small>{preset.detail}</small>
            </button>
          ))}
        </div>

        <div className="field-row">
          <label className="text-field">
            <span>Prefix <small>after T: uppercase or 9</small></span>
            <div className="prefix-input"><b className="fixed-prefix-token">T</b><input aria-invalid={Boolean(characterError)} aria-describedby="tron-character-help" value={prefix.slice(1)} onChange={(event) => updatePrefix(event.target.value)} maxLength={selectedPattern.prefixLength} placeholder={`${selectedPattern.prefixLength} ${selectedPattern.prefixLength === 1 ? "character" : "characters"}`} autoCapitalize="none" spellCheck={false} /><em>{prefix.length - 1}/{selectedPattern.prefixLength}</em></div>
          </label>
          <span className="field-connector">+</span>
          <label className="text-field">
            <span>Suffix <small>{selectedPattern.suffixLength} characters required</small></span>
            <div><input aria-invalid={Boolean(characterError)} aria-describedby="tron-character-help" value={suffix} onChange={(event) => updateSuffix(event.target.value)} maxLength={selectedPattern.suffixLength} placeholder="e.g. 7777" autoCapitalize="none" spellCheck={false} /><em>{suffix.length}/{selectedPattern.suffixLength}</em></div>
          </label>
        </div>

        <p className="character-help" id="tron-character-help"><ShieldIcon /> Every address starts with <strong>T</strong>. The next character must be <strong>uppercase or 9</strong>. TRON Base58 excludes <strong>0, O, I, and lowercase l</strong>.</p>
        {characterError && <p className="form-error character-error" role="alert"><AlertIcon />{characterError}</p>}

        <AddressPreview prefix={prefix || "T"} suffix={suffix} />

        <div className="default-match-note">
          <span><SparkIcon /></span>
          <p><strong>Optimized matching is included</strong><small>Case-insensitive and safe loose matching are applied automatically.</small></p>
          <em><CheckIcon /> On</em>
        </div>
      </section>

      <aside className="estimate-card panel">
        <span className="section-kicker">Live estimate</span>
        <div className="difficulty-header">
          <div><span className={`difficulty-dot ${difficulty.tone}`} /><strong>{difficulty.label}</strong></div>
          <span>{difficulty.level}/100 difficulty</span>
        </div>
        <div className="difficulty-track"><i style={{ width: `${difficulty.level}%` }} /></div>

        <div className="estimate-time">
          <span><ClockIcon /></span>
          <div><small>Expected search time</small><strong>{difficulty.wait}</strong></div>
        </div>

        <dl className="estimate-details">
          <div><dt>Likely completion</dt><dd>{difficulty.p95}</dd></div>
          <div><dt>Search mode</dt><dd>Optimized loose</dd></div>
          <div><dt>Compute pool</dt><dd><span className="status-dot" /> {fleet ? `${fleet.ready + fleet.searching}/${fleet.total} workers online` : "Checking API…"}</dd></div>
          <div><dt>Network</dt><dd>TRON Mainnet</dd></div>
        </dl>

        <div className="estimate-note"><ActivityIcon /><p>Time is an estimate. Vanity searches are random and may finish sooner or later.</p></div>

        {(error || externalError) && <p className="form-error"><AlertIcon />{error || externalError}</p>}
        <button className="primary-button" disabled={submitting} onClick={submit}>{submitting ? "Securing request…" : "Start generation"} {!submitting && <ArrowIcon />}</button>
        <p className="prototype-note">{fleet?.mode === "cuda" ? "Creates a real wallet job using every available local GPU." : "Creates a backend job. Simulator workers do not produce wallet results."}</p>
      </aside>
    </div>
  );
}

function GeneratingView({ job, fleet, onCancel, pollError }: { job: GenerationJob; fleet: GpuFleet | null; onCancel: () => Promise<void>; pollError: string }) {
  const [seconds, setSeconds] = useState(0);
  const [canceling, setCanceling] = useState(false);
  const phase = job.status;
  const pattern = `${job.prefix}${"•".repeat(Math.max(5, 34 - job.prefix.length - job.suffix.length))}${job.suffix}`;

  useEffect(() => {
    const started = new Date(job.started_at ?? job.created_at).getTime();
    const update = () => setSeconds(Math.max(0, Math.floor((Date.now() - started) / 1000)));
    update();
    const timer = window.setInterval(update, 1000);
    return () => window.clearInterval(timer);
  }, [job.created_at, job.started_at]);

  async function cancel() {
    setCanceling(true);
    try {
      await onCancel();
    } finally {
      setCanceling(false);
    }
  }

  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = seconds % 60;
  const isQueued = phase === "queued";
  const isVerifying = phase === "verifying";

  return (
    <section className="generation-layout">
      <div className="generation-hero panel">
        <div className="search-visual" aria-hidden="true">
          <span className="search-ring ring-one" />
          <span className="search-ring ring-two" />
          <span className="search-ring ring-three" />
          <span className="search-core"><SparkIcon /></span>
          <i className="particle p1" /><i className="particle p2" /><i className="particle p3" />
        </div>
        <span className="live-label"><i /> {isQueued ? "Waiting in queue" : isVerifying ? "CPU verification" : "Live worker search"}</span>
        <h2>{isQueued ? "Your request is safely queued" : isVerifying ? "Match found — verifying it now" : "Finding your one-of-one address"}</h2>
        <p>{isQueued ? "The scheduler will assign every available worker after the current job finishes." : isVerifying ? "A trusted CPU process is independently checking the key and address." : "The browser is polling verified progress from the generation API."}</p>

        <div className="generation-stats">
          <div><small>Elapsed</small><strong>{minutes.toString().padStart(2, "0")}:{remainingSeconds.toString().padStart(2, "0")}</strong></div>
          <div><small>Candidates checked</small><strong>{formatAttempts(job.attempts)}</strong></div>
          <div><small>Observed average</small><strong>{job.observed_rate === null ? isQueued ? "Waiting…" : "Measuring…" : formatRate(job.observed_rate)}</strong></div>
        </div>

        <div className="indeterminate-track"><i /></div>
        {pollError && <p className="form-error"><AlertIcon />{pollError}</p>}
        <button className="text-button danger" disabled={canceling} onClick={cancel}>{canceling ? "Canceling…" : "Cancel generation"}</button>
      </div>

      <aside className="job-card panel">
        <div className="job-title"><span><ActivityIcon /></span><div><small>Job {job.id.slice(0, 8).toUpperCase()}</small><strong>{phase.charAt(0).toUpperCase() + phase.slice(1)}</strong></div><i className="live-pulse" /></div>
        <div className="job-pattern"><small>Requested pattern</small><strong>{pattern}</strong><span>Case-insensitive loose match</span></div>
        <ol className="activity-list">
          <li className="complete"><span><CheckIcon /></span><div><strong>Request secured</strong><small>Server wallet seed encrypted</small></div></li>
          <li className={isQueued ? "active" : "complete"}><span>{!isQueued && <CheckIcon />}</span><div><strong>Workers assigned</strong><small>{fleet ? `${fleet.searching} searching · ${fleet.ready} ready` : "Waiting for fleet status"}</small></div></li>
          <li className={isVerifying ? "complete" : isQueued ? "" : "active"}><span>{isVerifying ? <CheckIcon /> : ""}</span><div><strong>Vanity search</strong><small>{isVerifying ? "Matching candidate found" : "Scanning unique key offsets"}</small></div></li>
          <li className={isVerifying ? "active" : ""}><span /><div><strong>Independent verification</strong><small>Address and checksum check</small></div></li>
        </ol>
        <div className="telegram-notice"><ActivityIcon /><p><strong>{fleet?.mode === "simulator" ? "Simulator mode" : "GPU fleet connected"}</strong><br />{fleet?.mode === "simulator" ? "Scheduling is live, but simulated workers never return a wallet." : `${fleet?.total ?? 0} local GPUs detected.`}</p></div>
      </aside>
    </section>
  );
}

function ReadyView({ job, onDelivery }: { job: GenerationJob; onDelivery: () => void }) {
  const [copied, setCopied] = useState(false);
  const address = job.result?.address ?? "";
  async function copyAddress() {
    await navigator.clipboard?.writeText(address);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1800);
  }
  return (
    <section className="ready-layout">
      <div className="success-panel panel">
        <span className="success-icon"><CheckIcon /></span>
        <span className="section-kicker">Match verified</span>
        <h2>Your vanity wallet is ready.</h2>
        <p>The GPU result passed an independent address and checksum verification.</p>
        <div className="result-address">
          <small>Your new public address</small>
          <div><strong>{address}</strong><button onClick={copyAddress} aria-label="Copy address">{copied ? <CheckIcon /> : <CopyIcon />}</button></div>
          {copied && <span className="copied-label">Address copied</span>}
        </div>
        <div className="match-summary">
          <div><span><CheckIcon /></span><p><small>Prefix match</small><strong>{job.prefix}</strong></p></div>
          <div><span><CheckIcon /></span><p><small>Suffix match</small><strong>{job.suffix}</strong></p></div>
          <div><span><ShieldIcon /></span><p><small>Verification</small><strong>Passed</strong></p></div>
        </div>
        {job.observed_rate !== null && <p className="completion-rate">Final observed average: {formatRate(job.observed_rate)} address candidates.</p>}
        <button className="primary-button wide" onClick={onDelivery}>Prepare secure download <ArrowIcon /></button>
      </div>

      <aside className="security-aside panel">
        <span className="security-illustration"><LockIcon /></span>
        <h3>Verified before delivery</h3>
        <p>The server assembles the final private key after independently verifying the GPU result. Your browser verifies the address again before encrypting the download.</p>
        <ul>
          <li><CheckIcon /> Encrypted server-side key storage</li>
          <li><CheckIcon /> Password-protected export</li>
          <li><CheckIcon /> Local ownership verification</li>
        </ul>
        <span className="demo-warning"><AlertIcon /> Store the encrypted wallet file and its password separately.</span>
      </aside>
    </section>
  );
}

function DeliveryView({ job, onDelivered }: { job: GenerationJob; onDelivered: () => void }) {
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [downloaded, setDownloaded] = useState(false);
  const [encrypting, setEncrypting] = useState(false);
  const [error, setError] = useState("");
  const address = job.result?.address ?? "";
  const strong = password.length >= 10;
  const matching = password === confirm && confirm.length > 0;

  async function downloadWallet() {
    if (!strong || !matching || !job.result) return;
    setEncrypting(true);
    setError("");
    try {
      const wallet = verifyServerWallet(job.result.private_key, address);
      const file = await encryptedWalletFile(wallet, password);
      const url = URL.createObjectURL(file);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `tronforge-${address}.json`;
      anchor.click();
      URL.revokeObjectURL(url);
      setDownloaded(true);
    } catch (caught) {
      setError(displayError(caught));
    } finally {
      setEncrypting(false);
    }
  }

  return (
    <section className="delivery-layout">
      <div className="delivery-card panel">
        <div className="panel-heading compact">
          <div><span className="section-kicker">03 — Secure your wallet</span><h2>Create your encrypted backup.</h2><p>This password protects the local wallet package. We cannot recover it.</p></div>
          <span className="lock-emblem"><LockIcon /></span>
        </div>

        <div className="delivery-address"><WalletIcon /><div><small>Verified destination</small><strong>{shortAddress(address)}</strong></div><span>Ready</span></div>

        <div className="password-grid">
          <label className="text-field"><span>Backup password</span><div><input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="At least 10 characters" /></div></label>
          <label className="text-field"><span>Confirm password</span><div><input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} placeholder="Repeat password" /></div></label>
        </div>
        <div className="password-meter"><i className={strong ? "good" : ""} /><i className={strong ? "good" : ""} /><i className={password.length >= 14 ? "good" : ""} /><span>{strong ? (password.length >= 14 ? "Strong" : "Good") : "Use 10+ characters"}</span></div>
        {confirm && !matching && <p className="form-error"><AlertIcon />Passwords do not match.</p>}
        {error && <p className="form-error"><AlertIcon />{error}</p>}

        <button className="primary-button wide" disabled={!strong || !matching || encrypting} onClick={downloadWallet}><DownloadIcon /> {encrypting ? "Verifying and encrypting…" : "Download encrypted wallet"}</button>
        <div className="download-notice"><ShieldIcon /><p><strong>Local secure delivery</strong><br />Your browser independently verifies the recovered key, then encrypts the wallet using AES-256-GCM.</p></div>

        {downloaded && (
          <div className="confirm-download">
            <span><CheckIcon /></span>
            <div><strong>Encrypted wallet downloaded</strong><p>Confirm that you saved it before the browser share is removed.</p></div>
            <button onClick={onDelivered}>I saved it <ArrowIcon /></button>
          </div>
        )}
      </div>
    </section>
  );
}

function FundingView({ address, onConfirm }: { address: string; onConfirm: (amount: number) => void }) {
  const [amount, setAmount] = useState("250");
  const [reviewing, setReviewing] = useState(false);
  const numeric = Number(amount);
  const valid = Number.isFinite(numeric) && numeric >= 1 && numeric <= 1500 && /^\d+(\.\d{0,6})?$/.test(amount);

  return (
    <section className="funding-layout">
      <div className="funding-card panel">
        <div className="panel-heading compact">
          <div><span className="section-kicker">04 — Fund your wallet</span><h2>{reviewing ? "Review your transfer." : "Choose a funding amount."}</h2><p>{reviewing ? "Confirm every detail before submitting the funding request." : "Send authorized USDT from the protected system funding wallet."}</p></div>
          <span className="usdt-emblem">₮</span>
        </div>

        {!reviewing ? (
          <>
            <label className="amount-input">
              <span>Funding amount</span>
              <div><strong>₮</strong><input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} /><em>USDT</em></div>
              <small>Minimum 1 USDT · Maximum 1,500 USDT</small>
            </label>
            <div className="amount-chips">
              {[50, 100, 250, 500, 1000, 1500].map((value) => <button className={numeric === value ? "active" : ""} key={value} onClick={() => setAmount(String(value))}>${value.toLocaleString()}</button>)}
            </div>
            {!valid && <p className="form-error"><AlertIcon />Enter an amount between 1 and 1,500 with up to six decimals.</p>}
            <div className="authorized-balance"><span><ShieldIcon /></span><div><small>Demo authorized balance</small><strong>1,500.00 USDT</strong></div><em>Verified</em></div>
            <button className="primary-button wide" disabled={!valid} onClick={() => setReviewing(true)}>Review funding <ArrowIcon /></button>
          </>
        ) : (
          <div className="review-block">
            <div className="review-amount"><small>You are sending</small><strong>{numeric.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 6 })} <span>USDT</span></strong></div>
            <dl>
              <div><dt>To wallet</dt><dd>{shortAddress(address)}</dd></div>
              <div><dt>Network</dt><dd>TRON · TRC-20</dd></div>
              <div><dt>Contract</dt><dd>TR7NHq...Lj6t <span>Verified</span></dd></div>
              <div><dt>Network resource</dt><dd>Calculated at signing</dd></div>
            </dl>
            <div className="review-warning"><AlertIcon /><p>This prototype will simulate the transfer. It cannot move real USDT.</p></div>
            <button className="primary-button wide" onClick={() => onConfirm(numeric)}>Confirm demo funding <LockIcon /></button>
            <button className="text-button" onClick={() => setReviewing(false)}>Go back and edit</button>
          </div>
        )}
      </div>
    </section>
  );
}

function ConfirmedView({ address, amount, onReset }: { address: string; amount: number; onReset: () => void }) {
  return (
    <section className="confirmed-layout">
      <div className="confirmed-card panel">
        <span className="confirmed-check"><CheckIcon /></span>
        <span className="section-kicker">Demo complete</span>
        <h2>Wallet funded successfully.</h2>
        <p>The simulated transaction passed every step of the proposed funding workflow.</p>
        <div className="confirmed-amount"><small>Amount funded</small><strong>{amount.toLocaleString(undefined, { minimumFractionDigits: 2 })} <span>USDT</span></strong></div>
        <dl className="confirmed-details">
          <div><dt>Destination</dt><dd>{shortAddress(address)}</dd></div>
          <div><dt>Transaction</dt><dd>8f2b91••••••••4a09 <ExternalIcon /></dd></div>
          <div><dt>Confirmation</dt><dd><span className="confirmed-status"><i /> Confirmed</span></dd></div>
        </dl>
        <button className="primary-button wide" onClick={onReset}>Create another wallet <SparkIcon /></button>
      </div>
    </section>
  );
}

function OrdersView({ jobs, loading, onCreate, onOpen }: { jobs: GenerationJob[]; loading: boolean; onCreate: () => void; onOpen: (job: GenerationJob) => void }) {
  return (
    <section className="content-view">
      <div className="view-intro"><div><span className="section-kicker">Your activity</span><h2>{loading ? "Loading requests…" : `${jobs.length} wallet ${jobs.length === 1 ? "request" : "requests"}.`}</h2><p>Live generation history from your authenticated API account.</p></div><button className="primary-button compact-button" onClick={onCreate}><SparkIcon /> New wallet</button></div>
      <div className="orders-table panel">
        <div className="table-head"><span>Order</span><span>Pattern</span><span>Created</span><span>Status</span><span /></div>
        {!loading && jobs.length === 0 && <div className="empty-orders"><WalletIcon /><strong>No wallet requests yet</strong><p>Create your first request to send it to the generation queue.</p></div>}
        {jobs.map((job) => (
          <div className="table-row" key={job.id}>
            <span><i className="order-logo"><WalletIcon /></i><strong>{job.id.slice(0, 8).toUpperCase()}</strong></span>
            <span><code>{job.prefix}{"•".repeat(8)}{job.suffix}</code><small>{job.pattern.replace("x", " × ")} loose match</small></span>
            <span>{new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(job.created_at))}</span>
            <span><b className={`status-badge ${job.status}`}>{job.status.replace("_", " ")}</b></span>
            <button aria-label="Open order" onClick={() => onOpen(job)}><ChevronIcon /></button>
          </div>
        ))}
      </div>
    </section>
  );
}

function SecurityView() {
  return (
    <section className="content-view">
      <div className="view-intro"><div><span className="section-kicker">Designed for one operator</span><h2>Server-managed generation.</h2><p>The backend owns wallet generation while encrypting private key material at rest and verifying every GPU result.</p></div></div>
      <div className="security-grid">
        {[
          { icon: KeyIcon, n: "01", title: "Server creates the wallet seed", text: "A cryptographically random private share is generated and immediately encrypted by the backend." },
          { icon: ActivityIcon, n: "02", title: "GPU searches with offsets", text: "Workers search the public point without receiving the encrypted base private share." },
          { icon: ShieldIcon, n: "03", title: "Server verifies and assembles", text: "The backend verifies the match, combines the key material, and encrypts the completed private key." },
          { icon: LockIcon, n: "04", title: "Browser protects the export", text: "Your browser verifies the delivered key and creates a password-protected wallet download." },
        ].map((item) => {
          const Icon = item.icon;
          return <article className="security-feature panel" key={item.n}><span className="feature-number">{item.n}</span><span className="feature-icon"><Icon /></span><h3>{item.title}</h3><p>{item.text}</p></article>;
        })}
      </div>
      <div className="trust-banner"><ShieldIcon /><div><strong>Production security review required</strong><p>This interface demonstrates the intended model. Cryptographic implementation and custody boundaries must be independently audited before mainnet use.</p></div></div>
    </section>
  );
}

function HelpView() {
  const [open, setOpen] = useState(0);
  const items = [
    ["What is a vanity TRON address?", "It is a valid TRON address discovered by searching for a visually recognizable prefix or suffix. It works like a normal address."],
    ["Why can’t you promise an exact wait time?", "Each candidate is random. Difficulty estimates describe probability, so an individual search can finish sooner or later than average."],
    ["Does case-insensitive matching change my address?", "No. It only allows more letter-case combinations during the search. Always use the exact capitalization in the delivered address."],
    ["How is my private key protected?", "The backend combines the encrypted server base key with the verified GPU offset. Your browser independently verifies the result and encrypts the downloaded backup with your password."],
  ];
  return (
    <section className="content-view help-layout">
      <div className="view-intro"><div><span className="section-kicker">Quick answers</span><h2>Understand every step.</h2><p>Start with the essentials of generation, delivery, and funding.</p></div></div>
      <div className="faq-list">
        {items.map(([question, answer], index) => <button className={`faq-item panel ${open === index ? "open" : ""}`} key={question} onClick={() => setOpen(open === index ? -1 : index)}><span><strong>{question}</strong>{open === index && <p>{answer}</p>}</span><i>+</i></button>)}
      </div>
      <div className="support-card"><span><TelegramIcon /></span><div><strong>Need a human?</strong><p>Contact support from Telegram without ever sharing a private key or recovery password.</p></div><button>Open Telegram <ExternalIcon /></button></div>
    </section>
  );
}

export function WalletStudio() {
  const [session, setSession] = useState<{ token: string; user: User } | null>();
  const [view, setView] = useState<View>("studio");
  const [flow, setFlow] = useState<Flow>("configure");
  const [jobs, setJobs] = useState<GenerationJob[]>([]);
  const [jobsLoading, setJobsLoading] = useState(true);
  const [fleet, setFleet] = useState<GpuFleet | null>(null);
  const [activeJob, setActiveJob] = useState<GenerationJob | null>(null);
  const [actionError, setActionError] = useState("");
  const [pollError, setPollError] = useState("");
  const [fundedAmount, setFundedAmount] = useState(250);

  const logout = useCallback(() => {
    void api.logout().catch(() => {
      // Sign-out still clears the local access token if the API is unreachable.
    });
    setSession(null);
    setJobs([]);
    setFleet(null);
    setActiveJob(null);
    setFlow("configure");
  }, []);

  useEffect(() => {
    let active = true;
    window.sessionStorage.removeItem("tronforge:access-token");
    api.refreshSession()
      .then(async (tokenResponse) => {
        const user = await api.me(tokenResponse.access_token);
        if (active) setSession({ token: tokenResponse.access_token, user });
      })
      .catch(() => {
        if (active) setSession(null);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!session) return;
    let active = true;
    let timer: number;

    async function refreshWorkspace() {
      try {
        const [jobList, gpuFleet] = await Promise.all([
          api.listJobs(session!.token),
          api.getGpuFleet(session!.token),
        ]);
        if (!active) return;
        setJobs(jobList.items);
        setFleet(gpuFleet);
        setJobsLoading(false);
      } catch (caught) {
        if (!active) return;
        if (caught instanceof ApiError && caught.status === 401) {
          logout();
          return;
        }
        setFleet(null);
        setActionError(displayError(caught));
        setJobsLoading(false);
      } finally {
        if (active) timer = window.setTimeout(refreshWorkspace, 4_000);
      }
    }

    refreshWorkspace();
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [logout, session]);

  const activeJobId = activeJob?.id;

  useEffect(() => {
    if (!session || !activeJobId || flow !== "generating") return;
    let active = true;
    let timer: number;
    const accessToken = session.token;
    const jobId = activeJobId;

    async function refreshActiveJob() {
      try {
        const refreshed = await api.getJob(accessToken, jobId);
        if (!active) return;
        setActiveJob(refreshed);
        setJobs((current) => current.map((job) => job.id === refreshed.id ? refreshed : job));
        setPollError("");
        if ((refreshed.status === "ready" || refreshed.status === "ownership_verified") && refreshed.result) {
          setFlow("ready");
          return;
        }
        if (TERMINAL_FAILURES.includes(refreshed.status)) {
          setActionError(refreshed.failure_message ?? `Generation ${refreshed.status.replace("_", " ")}.`);
          setFlow("configure");
          setActiveJob(null);
          return;
        }
      } catch (caught) {
        if (!active) return;
        if (caught instanceof ApiError && caught.status === 401) {
          logout();
          return;
        }
        setPollError(displayError(caught));
      }
      if (active) timer = window.setTimeout(refreshActiveJob, 1_000);
    }

    refreshActiveJob();
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [activeJobId, flow, logout, session]);

  function authenticated(token: string, user: User) {
    setSession({ token, user });
    setActionError("");
  }

  async function startGeneration(data: { pattern: PatternType; prefix: string; suffix: string }) {
    if (!session) return;
    setActionError("");
    try {
      const job = await api.createJob(session.token, data);
      setActiveJob(job);
      setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
      setFlow("generating");
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) logout();
      setActionError(displayError(caught));
    }
  }

  async function cancelGeneration() {
    if (!session || !activeJob) return;
    try {
      const canceled = await api.cancelJob(session.token, activeJob.id);
      setJobs((current) => current.map((job) => job.id === canceled.id ? canceled : job));
      setActiveJob(null);
      setFlow("configure");
      setActionError("Generation was canceled.");
    } catch (caught) {
      setPollError(displayError(caught));
    }
  }

  function reset() {
    setActiveJob(null);
    setActionError("");
    setFlow("configure");
    setView("studio");
  }

  function openJob(job: GenerationJob) {
    setView("studio");
    setActionError("");
    setActiveJob(job);
    if ((job.status === "ready" || job.status === "ownership_verified") && job.result) {
      setFlow("ready");
    } else if (["queued", "searching", "verifying"].includes(job.status)) {
      setFlow("generating");
    } else {
      setActiveJob(null);
      setFlow("configure");
      setActionError(job.failure_message ?? `This job is ${job.status.replace("_", " ")}.`);
    }
  }

  function finishDelivery() {
    if (!activeJob) return;
    setFlow("funding");
  }

  function navigate(next: View) {
    setView(next);
  }

  if (session === undefined) {
    return <main className="auth-page auth-loading"><span className="search-core"><SparkIcon /></span><p>Connecting to TronForge…</p></main>;
  }

  if (!session) return <AuthScreen onAuthenticated={authenticated} />;

  const resultAddress = activeJob?.result?.address ?? "";

  return (
    <main className="app-shell">
      <Sidebar view={view} setView={navigate} user={session.user} orderCount={jobs.length} onLogout={logout} />
      <div className="workspace">
        <Header view={view} fleet={fleet} />
        <div className="workspace-content">
          {view === "studio" && (
            <>
              <StepRail flow={flow} />
              {flow === "configure" && <ConfigureView onStart={startGeneration} fleet={fleet} externalError={actionError} />}
              {flow === "generating" && activeJob && <GeneratingView job={activeJob} fleet={fleet} onCancel={cancelGeneration} pollError={pollError} />}
              {flow === "ready" && activeJob?.result && <ReadyView job={activeJob} onDelivery={() => setFlow("delivery")} />}
              {flow === "delivery" && activeJob?.result && <DeliveryView job={activeJob} onDelivered={finishDelivery} />}
              {flow === "funding" && activeJob?.result && <FundingView address={resultAddress} onConfirm={(amount) => { setFundedAmount(amount); window.setTimeout(() => setFlow("confirmed"), 700); }} />}
              {flow === "confirmed" && <ConfirmedView address={resultAddress} amount={fundedAmount} onReset={reset} />}
            </>
          )}
          {view === "orders" && <OrdersView jobs={jobs} loading={jobsLoading} onCreate={reset} onOpen={openJob} />}
          {view === "security" && <SecurityView />}
          {view === "help" && <HelpView />}
        </div>
      </div>
      <nav className="mobile-nav" aria-label="Mobile navigation">
        {navItems.map((item) => { const Icon = item.icon; return <button key={item.id} className={view === item.id ? "active" : ""} onClick={() => navigate(item.id)}><Icon /><span>{item.label.split(" ")[0]}</span></button>; })}
      </nav>
    </main>
  );
}
