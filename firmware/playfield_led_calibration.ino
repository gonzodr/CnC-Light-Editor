// Standalone WS2812B index probe for the CnC Pinball playfield.
// Upload temporarily, then restore the normal firmware when calibration is done.
#include <FastLED.h>

#define DATA_PIN 3
#define NUM_LEDS 115
#define PLAYFIELD_LED_COUNT 59
#define BRIGHTNESS 64
#define LED_TYPE WS2812B
#define COLOR_ORDER GRB

CRGB leds[NUM_LEDS];
int selectedIndex = 0;

void showSelected() {
  fill_solid(leds, NUM_LEDS, CRGB::Black);
  leds[selectedIndex] = CRGB::White;
  FastLED.show();
  Serial.print(F("PLAYFIELD_LED_INDEX="));
  Serial.println(selectedIndex);
}

void setup() {
  Serial.begin(115200);
  FastLED.addLeds<LED_TYPE, DATA_PIN, COLOR_ORDER>(leds, NUM_LEDS)
    .setCorrection(TypicalLEDStrip);
  FastLED.setBrightness(BRIGHTNESS);
  showSelected();
  Serial.println(F("Commands: n=next, p=previous, 0..58=jump"));
}

void loop() {
  if (!Serial.available()) {
    return;
  }
  char command = Serial.peek();
  if (command == 'n' || command == 'N') {
    Serial.read();
    selectedIndex = (selectedIndex + 1) % PLAYFIELD_LED_COUNT;
  } else if (command == 'p' || command == 'P') {
    Serial.read();
    selectedIndex = (selectedIndex + PLAYFIELD_LED_COUNT - 1) % PLAYFIELD_LED_COUNT;
  } else if (isDigit(command)) {
    int requested = Serial.parseInt();
    if (requested >= 0 && requested < PLAYFIELD_LED_COUNT) {
      selectedIndex = requested;
    }
  } else {
    Serial.read();
    return;
  }
  while (Serial.available() && (Serial.peek() == '\r' || Serial.peek() == '\n')) {
    Serial.read();
  }
  showSelected();
}
