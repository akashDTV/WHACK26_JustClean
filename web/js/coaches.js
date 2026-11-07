// Coach personalities shared by the UI.
// Keep ids in sync with report/coaches.json on the Python side —
// the Python side sends the personality sliders to Claude.
window.COACHES = [
  {
    id: "hype", name: "Blaze", role: "Hype coach", initial: "B", color: "#FF8A3D",
    quote: "“YES! That arm was fire!”",
    voice: { rate: 1.15, pitch: 1.3 },
    sliders: { kindness: 0.9, technicality: 0.3, energy: 1.0, directness: 0.4 }
  },
  {
    id: "strict", name: "Gordon", role: "Strict teacher", initial: "G", color: "#5AB0FF",
    quote: "“IDIOT SANDWICH”",
    voice: { rate: 0.9, pitch: 0.8 },
    sliders: { kindness: 0.1, technicality: 0.8, energy: 0.7, directness: 1.0 }
  },
  {
    id: "technical", name: "Dr. Angle", role: "Technical analyst", initial: "A", color: "#B4AAFF",
    quote: "“18° off reference at 6.4 s.”",
    voice: { rate: 1.0, pitch: 1.0 },
    sliders: { kindness: 0.6, technicality: 1.0, energy: 0.3, directness: 0.7 }
  },
  {
    id: "gentle", name: "Coach Mira", role: "Gentle mentor", initial: "M", color: "#3DDC97",
    quote: "“Lovely holds — let’s polish the arms.”",
    voice: { rate: 0.95, pitch: 1.1 },
    sliders: { kindness: 1.0, technicality: 0.5, energy: 0.4, directness: 0.3 }
  }
];

window.coachById = (id) => window.COACHES.find((c) => c.id === id) || window.COACHES[0];
