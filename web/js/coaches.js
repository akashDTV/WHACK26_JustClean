// Coach personalities shared by the UI.
// catchphrase: what pops up while dancing. Shows when the live score stays above (or below)
// the number for HOLD seconds, then waits COOLDOWN seconds before it can show again.
window.CATCHPHRASE_TIMING = { hold_s: 1.5, cooldown_s: 6, grace_s: 1 };

// Keep ids in sync with report/coaches.json on the Python side —
// the Python side sends the personality sliders to Claude.
window.COACHES = [
  {
    id: "hype", name: "Priya", role: "Hype coach", initial: "B", color: "#FF8A3D", image: "img/coaches/Priya.png",
    quote: "“THAT'S IT! You understood the assignment! Every movement had energy, every beat had purpose, and that arm? Absolutely ICONIC!”",
    voice: { rate: 1.15, pitch: 1.3 },
    catchphrase: { text: "Amazing!", above: 70 },
    sliders: { kindness: 0.9, technicality: 0.3, energy: 1.0, directness: 0.4 }
  },
  {
    id: "strict", name: "Gordon", role: "Strict teacher", initial: "G", color: "#FF0000",image: "img/coaches/Gordon.png",
    quote: "“IDIOT SANDWICH”",
    voice: { rate: 0.9, pitch: 0.8 },
    catchphrase: { text: "Idiot sandwich!", below: 30 },
    sliders: { kindness: 0.1, technicality: 0.8, energy: 0.7, directness: 1.0 }
  },
  {
    id: "technical", name: "Ada", role: "Technical analyst", initial: "A", color: "#B4AAFF",image: "img/coaches/Ada.png",
    quote: "“That movement of yours is something more than merely physical; with precision, timing and technique, it shall become extraordinary.”",
    voice: { rate: 1.0, pitch: 1.0 },
    catchphrase: { text: "Splendid!", above: 80 },
    sliders: { kindness: 0.6, technicality: 1.0, energy: 0.3, directness: 0.7 }
  },
  {
    id: "gentle", name: "Bobby", role: "Gentle giant", initial: "B", color: "#228B22",image: "img/coaches/Bobby.png",
    quote: "“No Matter how many times you fail, you can always get up and try again.”",
    voice: { rate: 0.95, pitch: 1.1 },
    catchphrase: { text: "Clean!", above: 75 },
    sliders: { kindness: 1.0, technicality: 0.5, energy: 0.4, directness: 0.3 }
  }
];

window.coachById = (id) => window.COACHES.find((c) => c.id === id) || window.COACHES[0];

// Fills an avatar circle with the coach's image, or their letter if there's no image.
window.fillAvatar = function (div, coach) {
  div.style.background = coach.color;
  div.setAttribute("aria-hidden", "true");   // the name is shown next to it
  div.replaceChildren();
  if (coach.image) {
    const img = document.createElement("img");
    img.src = coach.image;
    img.alt = "";
    img.onerror = () => { div.textContent = coach.initial; };  // fallback
    div.appendChild(img);
  } else {
    div.textContent = coach.initial;
  }
};

// Makes a new avatar circle (used in lists).
window.makeAvatar = function (coach) {
  const div = document.createElement("div");
  div.className = "avatar";
  window.fillAvatar(div, coach);
  return div;
};