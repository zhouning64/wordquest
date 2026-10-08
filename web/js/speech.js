// speechSynthesis wrapper (spec §10): en-US voice when available, rate 0.9.
let voice = null;

export function speechSupported() {
  return typeof window !== "undefined"
    && "speechSynthesis" in window
    && typeof window.SpeechSynthesisUtterance === "function";
}

function pickVoice() {
  const voices = window.speechSynthesis.getVoices() || [];
  return voices.find((v) => v.lang === "en-US")
    || voices.find((v) => /^en[-_]US/i.test(v.lang))
    || voices.find((v) => /^en/i.test(v.lang))
    || null;
}

export function speak(text) {
  if (!speechSupported() || !text) return;
  const synth = window.speechSynthesis;
  synth.cancel();
  const u = new window.SpeechSynthesisUtterance(String(text));
  u.lang = "en-US";
  u.rate = 0.9;
  voice = voice || pickVoice();
  if (voice) u.voice = voice;
  synth.speak(u);
}

export function stopSpeaking() {
  if (speechSupported()) window.speechSynthesis.cancel();
}

// A 🔊 button that reads `text`; null when the browser has no speech support (the button hides).
export function speakButton(text, label = "Listen") {
  if (!speechSupported() || !text) return null;
  const b = document.createElement("button");
  b.type = "button";
  b.className = "speak";
  b.textContent = "🔊";
  b.setAttribute("aria-label", label);
  b.addEventListener("click", (e) => {
    e.stopPropagation();
    speak(text);
  });
  return b;
}

if (speechSupported() && typeof window.speechSynthesis.addEventListener === "function") {
  window.speechSynthesis.addEventListener("voiceschanged", () => { voice = pickVoice(); });
}
