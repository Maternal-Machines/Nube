/*
  Arduino sketch — controlled via Serial by a Python script.

  Python sends single-char commands:
    'I'  → IDLE      : both eyes half-closed (startup state)
    'O'  → OPEN      : both eyes fully open (listening)
    'P'  → SPEAK1    : speaking + eyes glow sporadic, out of sync
    'Q'  → SPEAK2    : speaking + eyes sparkle (shimmer)
    'D'  → DONE      : speaking finished, return to half-closed idle

  Arduino replies "READY\n" once setup() completes.

  Closing: fades brightness down to HALF_BRIGHTNESS then stops —
  never goes to black, so there is no flash or fade-back-up.
*/

#include <Adafruit_NeoPixel.h>
#include <math.h>

Adafruit_NeoPixel ring1(16, 6, NEO_GRB + NEO_KHZ800); // medium (left eye)
Adafruit_NeoPixel ring2(7,  4, NEO_GRB + NEO_KHZ800); // jewel  (mouth)
Adafruit_NeoPixel ring3(24, 5, NEO_GRB + NEO_KHZ800); // large  (right eye)

const uint32_t COLOR1 = ring1.Color(100, 140, 200);
const uint32_t COLOR2 = ring2.Color(0,   200, 100);
const uint32_t COLOR3 = ring3.Color(255, 80,  0);

#define MAX_BRIGHT_1    180
#define MAX_BRIGHT_2    180
#define MAX_BRIGHT_3     80
// Half-closed resting brightness — eyes stay here in IDLE, no black dip
#define HALF_BRIGHT_1    90    // ~50% of MAX_BRIGHT_1
#define HALF_BRIGHT_3    40    // ~50% of MAX_BRIGHT_3
#define GLOW_MIN          5.0f
#define FADE_STEP_TRANS  6.0f  // brightness units per frame during transitions
#define GLOW_SPEED        3.0f
#define AMBIENT_DIM      20.0f

#define SPEAK_MOUTH_CLOSED_MS  250
#define SPEAK_MOUTH_OPEN_MS    150
#define SPEAK_MOUTH_WIDE_MS    120
#define SPEAK_CENTRE_MS         80

// ── Transition / animation state ─────────────────────────────
enum TransPhase { TRANS_NONE, TRANS_OPENING, TRANS_CLOSING };
TransPhase transPhase = TRANS_NONE;

enum EyeMode { EYE_HALF, EYE_FULL, EYE_GLOW, EYE_SHIMMER };
EyeMode eyeMode     = EYE_HALF;
EyeMode eyeNextMode = EYE_HALF;   // mode to enter after open transition

// Current rendered brightness (brightness units, not 0–1 fraction)
float eyeBright1 = 0;
float eyeBright3 = 0;

// Pixel maps
bool r1HalfActive[16];
bool r3HalfActive[24];
float r1PixBright[16], r1PixDir[16];
float r3PixBright[24], r3PixDir[24];

// Glow pulse periods in ms — randomised when SPEAK1 starts (200-500ms each)
float r1GlowPeriod = 300.0f;
float r3GlowPeriod = 400.0f;

// ── Jewel / mouth ─────────────────────────────────────────────
enum J2Mode { J_OFF, J_HALF, J_FULL, J_SPEAK, J_SHIMMER };
J2Mode j2Mode = J_OFF;
float  j2Current[7], j2Target[7];
float  j2ShimBright[7], j2ShimDir[7];
int           speakState    = 0;
unsigned long speakStateEnd = 0;

// ── Helpers ──────────────────────────────────────────────────
void buildHalfMap(bool *active, int n, int offset) {
  int half = n / 2;
  for (int i = 0; i < n; i++) active[i] = ((i + offset) % n < half);
}
void buildFullMap(bool *active, int n) {
  for (int i = 0; i < n; i++) active[i] = true;
}
void setJewelTargets(bool centre, bool outer14, bool outer56) {
  j2Target[0] = centre  ? MAX_BRIGHT_2 : 0;
  for (int i = 1; i <= 3; i++) j2Target[i] = outer14 ? MAX_BRIGHT_2 : 0;
  for (int i = 4; i <= 6; i++) j2Target[i] = outer56 ? MAX_BRIGHT_2 : 0;
}

// ── Render both eye rings at explicit brightness values ───────
void renderEyes(float b1, float b3) {
  eyeBright1 = b1;
  eyeBright3 = b3;
  ring1.setBrightness((uint8_t)b1);
  for (int i = 0; i < 16; i++) ring1.setPixelColor(i, r1HalfActive[i] ? COLOR1 : 0);
  ring1.show();
  ring3.setBrightness((uint8_t)b3);
  for (int i = 0; i < 24; i++) ring3.setPixelColor(i, r3HalfActive[i] ? COLOR3 : 0);
  ring3.show();
}

// ── Jewel render ─────────────────────────────────────────────
void renderJewel() {
  ring2.setBrightness(255);
  for (int i = 0; i < 7; i++) {
    uint8_t b = (uint8_t)j2Current[i];
    ring2.setPixelColor(i, ring2.Color(0, (200*b)/MAX_BRIGHT_2, (100*b)/MAX_BRIGHT_2));
  }
  ring2.show();
}

// ── Speak tick ───────────────────────────────────────────────
void tickSpeak() {
  if (millis() < speakStateEnd) return;
  int roll = random(10);
  if      (roll <= 3) { speakState = 0; speakStateEnd = millis() + random(SPEAK_MOUTH_OPEN_MS,   SPEAK_MOUTH_OPEN_MS   * 2); }
  else if (roll <= 6) { speakState = 1; speakStateEnd = millis() + random(SPEAK_MOUTH_WIDE_MS,   SPEAK_MOUTH_WIDE_MS   * 2); }
  else if (roll <= 8) { speakState = 2; speakStateEnd = millis() + random(SPEAK_MOUTH_CLOSED_MS, SPEAK_MOUTH_CLOSED_MS * 2); }
  else                { speakState = 3; speakStateEnd = millis() + SPEAK_CENTRE_MS; }
  switch (speakState) {
    case 0: setJewelTargets(false, false, true);  break;
    case 1: setJewelTargets(false, true,  true);  break;
    case 2: setJewelTargets(false, false, false); break;
    case 3: setJewelTargets(true,  true,  true);  break;
  }
}

// ── Shimmer ──────────────────────────────────────────────────
void initShimmer(float *bright, float *dir, int n, float maxB) {
  for (int i = 0; i < n; i++) {
    bright[i] = random((int)GLOW_MIN, (int)maxB);
    dir[i]    = random(2) ? 1 : -1;
  }
}
void tickShimmerRing1() {
  ring1.setBrightness(255);
  for (int i = 0; i < 16; i++) {
    r1PixBright[i] += r1PixDir[i] * GLOW_SPEED;
    if (r1PixBright[i] >= MAX_BRIGHT_1) { r1PixBright[i] = MAX_BRIGHT_1; r1PixDir[i] = -1; }
    if (r1PixBright[i] <= 0)            { r1PixBright[i] = 0;            r1PixDir[i] =  1; }
    uint8_t b = (uint8_t)r1PixBright[i];
    ring1.setPixelColor(i, ring1.Color((100*b)/MAX_BRIGHT_1,(140*b)/MAX_BRIGHT_1,(200*b)/MAX_BRIGHT_1));
  }
  ring1.show();
}
void tickShimmerRing3() {
  ring3.setBrightness(255);
  for (int i = 0; i < 24; i++) {
    r3PixBright[i] += r3PixDir[i] * GLOW_SPEED;
    if (r3PixBright[i] >= MAX_BRIGHT_3) { r3PixBright[i] = MAX_BRIGHT_3; r3PixDir[i] = -1; }
    if (r3PixBright[i] <= 0)            { r3PixBright[i] = 0;            r3PixDir[i] =  1; }
    uint8_t b = (uint8_t)r3PixBright[i];
    ring3.setPixelColor(i, ring3.Color((255*b)/MAX_BRIGHT_3,(80*b)/MAX_BRIGHT_3,0));
  }
  ring3.show();
}
void tickShimmerJewel() {
  ring2.setBrightness(255);
  for (int i = 0; i < 7; i++) {
    j2ShimBright[i] += j2ShimDir[i] * GLOW_SPEED;
    if (j2ShimBright[i] >= MAX_BRIGHT_2) { j2ShimBright[i] = MAX_BRIGHT_2; j2ShimDir[i] = -1; }
    if (j2ShimBright[i] <= 0)            { j2ShimBright[i] = 0;            j2ShimDir[i] =  1; }
    uint8_t b = (uint8_t)j2ShimBright[i];
    ring2.setPixelColor(i, ring2.Color(0,(200*b)/MAX_BRIGHT_2,(100*b)/MAX_BRIGHT_2));
  }
  ring2.show();
}

// ── High-level state commands ─────────────────────────────────

// 'I' / 'D' IDLE — fade down to HALF_BRIGHT and stop on half-map
// Never goes to black → no flash, no fade-back-up
void cmdIdle() {
  buildHalfMap(r1HalfActive, 16, 7);
  buildHalfMap(r3HalfActive, 24, 2);
  transPhase = TRANS_CLOSING;
  eyeMode    = EYE_HALF;
  j2Mode     = J_HALF;
  setJewelTargets(false, false, true);
}

// 'O' OPEN — fade up from current brightness to full, full pixel map
void cmdOpen() {
  buildFullMap(r1HalfActive, 16);
  buildFullMap(r3HalfActive, 24);
  transPhase   = TRANS_OPENING;
  eyeNextMode  = EYE_FULL;
  j2Mode       = J_FULL;
  setJewelTargets(false, true, true);
}

// 'P' SPEAK1 — fade up to full then sporadic glow + speak
void cmdSpeak1() {
  buildFullMap(r1HalfActive, 16);
  buildFullMap(r3HalfActive, 24);
  transPhase      = TRANS_OPENING;
  eyeNextMode     = EYE_GLOW;
  j2Mode          = J_SPEAK;
  speakStateEnd   = 0;
  speakState      = 0;
  r1GlowPeriod = random(800, 1200);
  r3GlowPeriod = random(800, 1400);
  setJewelTargets(false, false, true);
}

// 'Q' SPEAK2 — fade up to full then shimmer + speak
void cmdSpeak2() {
  buildFullMap(r1HalfActive, 16);
  buildFullMap(r3HalfActive, 24);
  transPhase    = TRANS_OPENING;
  eyeNextMode   = EYE_SHIMMER;
  j2Mode        = J_SPEAK;
  speakStateEnd = 0;
  speakState    = 0;
  setJewelTargets(false, false, true);
}

// ── Setup ─────────────────────────────────────────────────────
void setup() {
  Serial.begin(9600);
  ring1.begin(); ring1.show();
  ring2.begin(); ring2.show();
  ring3.begin(); ring3.show();
  randomSeed(analogRead(A0));

  buildHalfMap(r1HalfActive, 16, 7);
  buildHalfMap(r3HalfActive, 24, 2);
  for (int i = 0; i < 7; i++) { j2Current[i] = 0; j2Target[i] = 0; }

  eyeBright1 = 0;
  eyeBright3 = 0;
  eyeMode    = EYE_HALF;
  transPhase = TRANS_OPENING;   // fade up into idle on startup
  eyeNextMode = EYE_HALF;
  j2Mode = J_HALF;
  setJewelTargets(false, false, true);

  Serial.println("READY");
}

// ── Loop ──────────────────────────────────────────────────────
void loop() {

  // Serial commands
  if (Serial.available()) {
    char c = Serial.read();
    switch (c) {
      case 'I': cmdIdle();   break;
      case 'O': cmdOpen();   break;
      case 'P': cmdSpeak1(); break;
      case 'Q': cmdSpeak2(); break;
      case 'D': cmdIdle();   break;
    }
  }

  // ── Transition tick ───────────────────────────────────────
  if (transPhase == TRANS_CLOSING) {
    // Fade down toward HALF_BRIGHT — never to black, never flashes
    bool done1 = false, done3 = false;
    if (eyeBright1 > HALF_BRIGHT_1) {
      eyeBright1 -= FADE_STEP_TRANS;
      if (eyeBright1 < HALF_BRIGHT_1) eyeBright1 = HALF_BRIGHT_1;
    } else { done1 = true; }
    if (eyeBright3 > HALF_BRIGHT_3) {
      eyeBright3 -= FADE_STEP_TRANS;
      if (eyeBright3 < HALF_BRIGHT_3) eyeBright3 = HALF_BRIGHT_3;
    } else { done3 = true; }
    renderEyes(eyeBright1, eyeBright3);
    if (done1 && done3) transPhase = TRANS_NONE;

  } else if (transPhase == TRANS_OPENING) {
    // Fade up toward MAX_BRIGHT
    bool done1 = false, done3 = false;
    if (eyeBright1 < MAX_BRIGHT_1) {
      eyeBright1 += FADE_STEP_TRANS;
      if (eyeBright1 > MAX_BRIGHT_1) eyeBright1 = MAX_BRIGHT_1;
    } else { done1 = true; }
    if (eyeBright3 < MAX_BRIGHT_3) {
      eyeBright3 += FADE_STEP_TRANS;
      if (eyeBright3 > MAX_BRIGHT_3) eyeBright3 = MAX_BRIGHT_3;
    } else { done3 = true; }
    renderEyes(eyeBright1, eyeBright3);
    if (done1 && done3) {
      eyeMode    = eyeNextMode;
      transPhase = TRANS_NONE;
      // Initialise shimmer if needed
      if (eyeMode == EYE_SHIMMER) {
        initShimmer(r1PixBright, r1PixDir, 16, MAX_BRIGHT_1);
        initShimmer(r3PixBright, r3PixDir, 24, MAX_BRIGHT_3);
      }
    }

  } else {
    // ── Running eye animations ────────────────────────────────
    switch (eyeMode) {

      case EYE_HALF: {
        // Resting at HALF_BRIGHT — hold steady
        renderEyes(HALF_BRIGHT_1, HALF_BRIGHT_3);
        break;
      }

      case EYE_FULL: {
        renderEyes(MAX_BRIGHT_1, MAX_BRIGHT_3);
        break;
      }

      case EYE_GLOW: {
        // Each eye runs its own sine pulse with a random period (200-500ms).
        // They start offset so one is rising while the other falls.
        unsigned long now = millis();

        // Ring 1 — sine wave, period r1GlowPeriod ms
        float phase1  = (float)(now % (unsigned long)r1GlowPeriod) / r1GlowPeriod;
        eyeBright1    = GLOW_MIN + (MAX_BRIGHT_1 - GLOW_MIN) * (0.5f - 0.5f * cos(phase1 * 2.0f * 3.14159f));

        // Ring 3 — sine wave, period r3GlowPeriod ms, started offset by half a cycle
        float phase3  = (float)((now + (unsigned long)(r3GlowPeriod * 0.5f)) % (unsigned long)r3GlowPeriod) / r3GlowPeriod;
        eyeBright3    = GLOW_MIN + (MAX_BRIGHT_3 - GLOW_MIN) * (0.5f - 0.5f * cos(phase3 * 2.0f * 3.14159f));

        ring1.setBrightness((uint8_t)eyeBright1);
        for (int i = 0; i < 16; i++) ring1.setPixelColor(i, r1HalfActive[i] ? COLOR1 : 0);
        ring1.show();
        ring3.setBrightness((uint8_t)eyeBright3);
        for (int i = 0; i < 24; i++) ring3.setPixelColor(i, r3HalfActive[i] ? COLOR3 : 0);
        ring3.show();
        break;
      }

      case EYE_SHIMMER: {
        tickShimmerRing1();
        tickShimmerRing3();
        eyeBright1 = MAX_BRIGHT_1;
        eyeBright3 = MAX_BRIGHT_3;
        break;
      }
    }
  }

  // ── Jewel animation ────────────────────────────────────────
  if (j2Mode == J_SHIMMER) {
    tickShimmerJewel();
  } else {
    if (j2Mode == J_SPEAK) tickSpeak();
    bool needsRender = false;
    for (int i = 0; i < 7; i++) {
      if (j2Current[i] != j2Target[i]) {
        float step = (j2Target[i] < j2Current[i]) ? 6.0f : 2.0f;
        if (j2Current[i] < j2Target[i]) { j2Current[i] += step; if (j2Current[i] > j2Target[i]) j2Current[i] = j2Target[i]; }
        else                             { j2Current[i] -= step; if (j2Current[i] < j2Target[i]) j2Current[i] = j2Target[i]; }
        needsRender = true;
      }
    }
    if (j2Mode != J_OFF || needsRender) renderJewel();
  }

  delay(20);
}
