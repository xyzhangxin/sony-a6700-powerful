/*

See scripts/push_payload.py for usage

See MULTI_* defines for pinout (they go to the Sony multi-port connector).

Build flags, passed by the Makefile's DEFINES: OPEN_DRAIN drives the multi-port
tx line against the camera's pull-up instead of pushing it to 3v3, and UART_TX
lets the payload console transmit (which needs a level shifter).

*/

#include <string.h>

#include <hardware/clocks.h>
#include <hardware/dma.h>
#include <hardware/pio.h>
#include <hardware/structs/sio.h>
#include <hardware/timer.h>

#include "serialboot.pio.h"

#define MULTI_TX  28
#define MULTI_RX  29
#define MULTI_RST 27

#define LINK_BAUD 48000u

#define RESET_PULSE_US 10u
#define STARVE_MS      1200u

#ifndef UART_TX
// Serial1 falls back to its variant default when its tx pin is left unset, which must not be the multi-port line.
static_assert(MULTI_TX != PIN_SERIAL1_TX, "MULTI_TX collides with the default Serial1 tx pin");
#endif

#define VERSION "a6700_pio_od 2"
#ifndef OPEN_DRAIN
#error This derived build requires OPEN_DRAIN
#endif

enum {
  CMD_PING    = 0x01,
  CMD_RESET   = 0x02,
  CMD_CARRIER = 0x03,
  CMD_BLOB    = 0x04,
  CMD_BAUD    = 0x05,
  CMD_UART    = 0x06,

  EVT_ACK  = 0x81,
  EVT_ERR  = 0x82,
  EVT_UART = 0x83,
};

enum {
  MODE_IDLE,
  MODE_CARRIER,
  MODE_UART,
};

#define MAX_RECORD 4100u
#define MAX_PKT    MAX_RECORD
#define UART_CHUNK 512u
#define BITS_PER_BYTE 11u
#define MAX_BITWORDS (1u + (MAX_RECORD * BITS_PER_BYTE + 31u) / 32u)

static uint8_t  rx_buf[MAX_PKT];
static uint8_t  uart_buf[UART_CHUNK];
static uint32_t bitbuf[MAX_BITWORDS];

static uint8_t  mode;
static bool     blob_pending;
static uint32_t last_blob_ms;

static PIO      pio = pio0;
static int      pio_sm = -1;
static uint     pio_off;
static int      dma_chan = -1;

static uint8_t  rx_hdr_got;
static uint8_t  rx_type;
static uint16_t rx_len;
static uint16_t rx_body_got;
static uint16_t rx_skip;

static uint32_t le32(const uint8_t *p) {
  return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static void send_pkt(uint8_t type, const void *data, uint16_t len) {
  uint8_t hdr[3] = { type, (uint8_t)len, (uint8_t)(len >> 8) };
  Serial.write(hdr, sizeof(hdr));
  if (len) {
    Serial.write((const uint8_t *)data, len);
  }
}

static void send_text(uint8_t type, const char *s) {
  send_pkt(type, s, (uint16_t)strlen(s));
}

static void pulse_reset(void) {
  sio_hw->gpio_clr    = 1u << MULTI_RST;
  sio_hw->gpio_oe_set = 1u << MULTI_RST;
  busy_wait_us_32(RESET_PULSE_US);
  sio_hw->gpio_oe_clr = 1u << MULTI_RST;
}

static void carrier_start(void) {
#ifdef OPEN_DRAIN
  pio_sm_config c = serialboot_od_program_get_default_config(pio_off);
#else
  pio_sm_config c = serialboot_pp_program_get_default_config(pio_off);
#endif
  sm_config_set_set_pins(&c, MULTI_TX, 1);
  sm_config_set_out_pins(&c, MULTI_TX, 1);
  sm_config_set_out_shift(&c, true, true, 32);
  sm_config_set_clkdiv(&c, (float)clock_get_hz(clk_sys) / (2.0f * LINK_BAUD));
  sm_config_set_mov_status(&c, STATUS_TX_LESSTHAN, 1);

  pio_gpio_init(pio, MULTI_TX);
#ifdef OPEN_DRAIN
  pio_sm_set_pins_with_mask(pio, pio_sm, 0, 1u << MULTI_TX);
  pio_sm_set_consecutive_pindirs(pio, pio_sm, MULTI_TX, 1, false);
#else
  pio_sm_set_consecutive_pindirs(pio, pio_sm, MULTI_TX, 1, true);
#endif
  pio_sm_init(pio, pio_sm, pio_off, &c);
  pio_sm_clear_fifos(pio, pio_sm);
  pio_interrupt_clear(pio, 0);
  pio_sm_set_enabled(pio, pio_sm, true);
}

static void carrier_stop(void) {
  if (dma_channel_is_busy(dma_chan)) {
    dma_channel_abort(dma_chan);
  }
  pio_sm_set_enabled(pio, pio_sm, false);
  pinMode(MULTI_TX, INPUT);
  blob_pending = false;
}

static uint32_t build_bits(const uint8_t *rec, uint32_t len) {
  uint32_t nbits = (len * BITS_PER_BYTE + 31u) & ~31u;
  uint32_t nwords = nbits / 32u;

  memset(bitbuf + 1, 0, nwords * sizeof(uint32_t));
  bitbuf[0] = nbits - 1u;

  uint32_t n = 0;
  for (uint32_t i = 0; i < len; i++) {
    uint8_t v = rec[i];
    uint32_t frame = 1u << 10;
    uint32_t x = 0;
    for (uint32_t b = 0; b < 8; b++) {
      uint32_t bit = (v >> b) & 1u;
      x ^= bit;
      frame |= bit << (1u + b);
    }
    frame |= (x ? 0u : 1u) << 9;

    for (uint32_t b = 0; b < BITS_PER_BYTE; b++) {
      if (frame & (1u << b)) {
        bitbuf[1u + (n >> 5)] |= 1u << (n & 31u);
      }
      n++;
    }
  }

  uint32_t npad = nbits - n;
  for (uint32_t p = 0; n < nbits; p++, n++) {
    if (((npad - 1u - p) >> 3) & 1u) {
      bitbuf[1u + (n >> 5)] |= 1u << (n & 31u);
    }
  }

#ifdef OPEN_DRAIN
  // the state machine drives pindirs, so the wire level is the complement of each bit
  for (uint32_t w = 1; w <= nwords; w++) {
    bitbuf[w] = ~bitbuf[w];
  }
#endif

  return 1u + nwords;
}

static void blob_send(uint32_t nwords) {
  dma_channel_config c = dma_channel_get_default_config(dma_chan);
  channel_config_set_transfer_data_size(&c, DMA_SIZE_32);
  channel_config_set_read_increment(&c, true);
  channel_config_set_write_increment(&c, false);
  channel_config_set_dreq(&c, pio_get_dreq(pio, pio_sm, true));
  dma_channel_configure(dma_chan, &c, &pio->txf[pio_sm], bitbuf, nwords, true);
}

static void uart_start(uint32_t baud) {
  Serial1.setFIFOSize(1024);
#ifdef UART_TX
  Serial1.setTX(MULTI_TX);
#endif
  Serial1.setRX(MULTI_RX);
  Serial1.begin(baud);
}

static void uart_stop(void) {
  Serial1.end();
  pinMode(MULTI_TX, INPUT);
}

static void mode_set(uint8_t m, uint32_t baud) {
  switch (mode) {
  case MODE_CARRIER: carrier_stop(); break;
  case MODE_UART:    uart_stop();    break;
  }
  switch (m) {
  case MODE_CARRIER: carrier_start();  break;
  case MODE_UART:    uart_start(baud); break;
  }
  mode = m;
}

static void handle_pkt(uint8_t type, uint8_t *d, uint16_t len) {
  switch (type) {
  case CMD_PING:
    send_text(EVT_ACK, VERSION);
    break;

  case CMD_RESET:
    pulse_reset();
    send_pkt(EVT_ACK, NULL, 0);
    break;

  case CMD_CARRIER:
    if (len < 1) {
      send_text(EVT_ERR, "short CMD_CARRIER");
      break;
    }
    if (d[0]) {
      if (mode != MODE_CARRIER) {
        mode_set(MODE_CARRIER, 0);
      }
      last_blob_ms = millis();
    } else {
      mode_set(MODE_IDLE, 0);
    }
    send_pkt(EVT_ACK, NULL, 0);
    break;

  case CMD_BLOB:
    if (mode != MODE_CARRIER) {
      send_text(EVT_ERR, "carrier off");
      break;
    }
    if (blob_pending) {
      send_text(EVT_ERR, "slot busy");
      break;
    }
    if (len > MAX_RECORD) {
      send_text(EVT_ERR, "record too long");
      break;
    }
    blob_send(build_bits(d, len));
    blob_pending = true;
    last_blob_ms = millis();
    break;

  case CMD_BAUD: {
    if (len < 4) {
      send_text(EVT_ERR, "short CMD_BAUD");
      break;
    }
    uint32_t baud = le32(d);
    if (baud && mode == MODE_CARRIER) {
      send_text(EVT_ERR, "carrier on");
      break;
    }
    mode_set(baud ? MODE_UART : MODE_IDLE, baud);
    send_pkt(EVT_ACK, NULL, 0);
    break;
  }

  case 0x07: // Isolated bench pull-up, only while idle.
    if (mode != MODE_IDLE || len != 1) { send_text(EVT_ERR, "bench requires idle"); break; }
    gpio_set_pulls(MULTI_TX, d[0] != 0, false);
    send_pkt(EVT_ACK, NULL, 0);
    break;

  case 0x08: { // Read existing TX pin only; do not change the PIO or direction.
    uint8_t high = 0;
    const uint32_t start = time_us_32();
    while ((uint32_t)(time_us_32() - start) < 2000) {
      if (gpio_get(MULTI_TX)) high = 1;
    }
    send_pkt(EVT_ACK, &high, 1);
    break;
  }

  case CMD_UART:
    if (mode != MODE_UART) {
      send_text(EVT_ERR, "uart not up");
      break;
    }
#ifdef UART_TX
    Serial1.write(d, len);
#else
    send_text(EVT_ERR, "uart tx disabled");
#endif
    break;

  default:
    send_text(EVT_ERR, "unknown command");
    break;
  }
}

static void poll_host(void) {
  while (Serial.available()) {
    int c = Serial.read();
    if (c < 0) {
      break;
    }

    if (rx_skip) {
      rx_skip--;
      continue;
    }

    if (rx_hdr_got < 3) {
      if (rx_hdr_got == 0) {
        rx_type = (uint8_t)c;
      } else if (rx_hdr_got == 1) {
        rx_len = (uint16_t)c;
      } else {
        rx_len |= (uint16_t)c << 8;
      }
      rx_hdr_got++;
      if (rx_hdr_got == 3) {
        rx_body_got = 0;
        if (rx_len > MAX_PKT) {
          send_text(EVT_ERR, "packet too long");
          rx_skip    = rx_len;
          rx_hdr_got = 0;
        } else if (rx_len == 0) {
          handle_pkt(rx_type, rx_buf, 0);
          rx_hdr_got = 0;
        }
      }
    } else {
      rx_buf[rx_body_got++] = (uint8_t)c;
      if (rx_body_got == rx_len) {
        handle_pkt(rx_type, rx_buf, rx_len);
        rx_hdr_got = 0;
      }
    }
  }
}

void setup(void) {
  pinMode(MULTI_TX,  INPUT);
  pinMode(MULTI_RX,  INPUT);
  pinMode(MULTI_RST, INPUT);
  gpio_disable_pulls(MULTI_TX);

  pio_sm   = pio_claim_unused_sm(pio, true);
#ifdef OPEN_DRAIN
  pio_off  = pio_add_program(pio, &serialboot_od_program);
#else
  pio_off  = pio_add_program(pio, &serialboot_pp_program);
#endif
  dma_chan = dma_claim_unused_channel(true);

  Serial.begin(115200);
}

void loop(void) {
  poll_host();

  if (blob_pending && pio_interrupt_get(pio, 0)) {
    pio_interrupt_clear(pio, 0);
    blob_pending = false;
    send_pkt(EVT_ACK, NULL, 0);
  }

  if (mode == MODE_CARRIER && !blob_pending &&
      (uint32_t)(millis() - last_blob_ms) > STARVE_MS) {
    mode_set(MODE_IDLE, 0);
    send_text(EVT_ERR, "starved");
  }

  if (mode == MODE_UART) {
    int n = Serial1.available();
    if (n > 0) {
      if (n > (int)sizeof(uart_buf)) {
        n = (int)sizeof(uart_buf);
      }
      for (int i = 0; i < n; i++) {
        uart_buf[i] = (uint8_t)Serial1.read();
      }
      send_pkt(EVT_UART, uart_buf, (uint16_t)n);
    }
  }
}
