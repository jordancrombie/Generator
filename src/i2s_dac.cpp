#include "i2s_dac.h"

#include <math.h>
#include "driver/rtc_io.h"
#include "esp_private/periph_ctrl.h"
#include "hal/dac_ll.h"
#include "soc/i2s_struct.h"
#include "soc/rtc.h"
#include "clk_ctrl_os.h"

namespace i2s_dac {

static constexpr double XTAL_HZ = 40e6;
static constexpr int APLL_ODIV = 1;
// In 16-bit LCD mode with clkm_div_num = 1 and bck_div = 1, the DAC sample
// rate is APLL / 4.
static constexpr double APLL_PER_SAMPLE = 4.0;

double set_sample_rate(double sample_hz) {
    // f_apll = xtal * (4 + sdm2 + sdm1/2^8 + sdm0/2^16) / ((odiv + 2) * 2)
    double mult = sample_hz * APLL_PER_SAMPLE * (APLL_ODIV + 2) * 2 / XTAL_HZ - 4.0;
    uint32_t sdm2 = (uint32_t)floor(mult);
    uint32_t frac = (uint32_t)lround((mult - sdm2) * 65536.0);
    if (frac >= 65536) { frac -= 65536; sdm2++; }
    uint32_t sdm1 = frac >> 8, sdm0 = frac & 0xFF;
    static bool powered = false;
    if (!powered) { periph_rtc_apll_acquire(); powered = true; }  // must be on before coeff_set
    rtc_clk_apll_coeff_set(APLL_ODIV, sdm0, sdm1, sdm2);
    double real = XTAL_HZ * (4.0 + sdm2 + frac / 65536.0) / ((APLL_ODIV + 2) * 2) / APLL_PER_SAMPLE;
    return real;
}

void start(lldesc_t *ring) {
    periph_module_enable(PERIPH_I2S0_MODULE);

    I2S0.conf.val = 0;
    I2S0.conf.tx_reset = 1;      I2S0.conf.tx_reset = 0;
    I2S0.conf.tx_fifo_reset = 1; I2S0.conf.tx_fifo_reset = 0;
    I2S0.lc_conf.out_rst = 1;    I2S0.lc_conf.out_rst = 0;
    I2S0.lc_conf.ahbm_rst = 1;   I2S0.lc_conf.ahbm_rst = 0;
    I2S0.lc_conf.ahbm_fifo_rst = 1; I2S0.lc_conf.ahbm_fifo_rst = 0;

    I2S0.conf.tx_right_first = 1;
    I2S0.conf.tx_mono = 1;
    I2S0.conf2.val = 0;
    I2S0.conf2.lcd_en = 1;
    I2S0.fifo_conf.dscr_en = 1;
    I2S0.fifo_conf.tx_fifo_mod_force_en = 1;
    I2S0.fifo_conf.tx_fifo_mod = 1;          // 16-bit single channel
    I2S0.conf_chan.tx_chan_mod = 1;
    I2S0.sample_rate_conf.tx_bits_mod = 16;
    I2S0.lc_conf.outdscr_burst_en = 1;
    I2S0.lc_conf.out_data_burst_en = 1;

    I2S0.clkm_conf.clkm_div_num = 1;
    I2S0.clkm_conf.clkm_div_b = 0;
    I2S0.clkm_conf.clkm_div_a = 1;
    I2S0.sample_rate_conf.tx_bck_div_num = 1;
    I2S0.clkm_conf.clka_en = 1;              // clock from APLL

    // GPIO25 as floating RTC pad, DAC1 powered and fed from I2S DMA.
    rtc_gpio_init(GPIO_NUM_25);
    rtc_gpio_set_direction(GPIO_NUM_25, RTC_GPIO_MODE_DISABLED);
    rtc_gpio_pullup_dis(GPIO_NUM_25);
    rtc_gpio_pulldown_dis(GPIO_NUM_25);
    dac_ll_power_on(DAC_CHAN_0);
    dac_ll_rtc_sync_by_adc(false);
    dac_ll_digi_enable_dma(true);

    I2S0.out_link.addr = (uint32_t)ring;
    I2S0.out_link.start = 1;
    I2S0.conf.tx_start = 1;
}

uint32_t current_descriptor() { return I2S0.out_link_dscr; }

void force_dc(int code) {
    if (code < 0) { dac_ll_digi_enable_dma(true); return; }
    dac_ll_digi_enable_dma(false);
    dac_ll_update_output_value(DAC_CHAN_0, (uint8_t)code);
}

}  // namespace i2s_dac
