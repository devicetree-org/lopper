# /*
# * Copyright (c) 2022 - 2023 Advanced Micro Devices, Inc. All Rights Reserved.
# *
# * Author:
# *       Madhav Bhatt <madhav.bhatt@amd.com>
# *
# * SPDX-License-Identifier: BSD-3-Clause
# */

rpu0_as_power_management_master = True
rpu1_as_power_management_master = True
apu_as_power_management_master = True
rpu0_as_reset_management_master = True
rpu1_as_reset_management_master = True
apu_as_reset_management_master = True
rpu0_as_overlay_config_master = False
rpu1_as_overlay_config_master = False
apu_as_overlay_config_master = False

subsys_str = "PMU Firmware:PMU|Secure Subsystem:"

node_map = {
	"NODE_APU" : { "label" : "NODE_APU", "periph" : "psu_cortexa53_0", "type" : "processor" },
	"NODE_APU_0" : { "label" : "NODE_APU_0", "periph" : "psu_cortexa53_0", "type" : "processor" },
	"NODE_APU_1" : { "label" : "NODE_APU_1", "periph" : "psu_cortexa53_1", "type" : "processor" },
	"NODE_APU_2" : { "label" : "NODE_APU_2", "periph" : "psu_cortexa53_2", "type" : "processor" },
	"NODE_APU_3" : { "label" : "NODE_APU_3", "periph" : "psu_cortexa53_3", "type" : "processor" },
	"NODE_RPU" : { "label" : "NODE_RPU", "periph" : "psu_cortexr5_0", "type" : "processor" },
	"NODE_RPU_0" : { "label" : "NODE_RPU_0", "periph" : "psu_cortexr5_0", "type" : "processor" },
	"NODE_RPU_1" : { "label" : "NODE_RPU_1", "periph" : "psu_cortexr5_1", "type" : "processor" },
	"NODE_PLD" : { "label" : "NODE_PLD", "periph" : "NA", "type" : "power" },
	"NODE_FPD" : { "label" : "NODE_FPD", "periph" : "NA", "type" : "power" },
	"NODE_OCM_BANK_0" : { "label" : "NODE_OCM_BANK_0", "periph" : "psu_ocm_0", "type" : "memory", "base_addr" : [0xfffc0000]},
	"NODE_OCM_BANK_1" : { "label" : "NODE_OCM_BANK_1", "periph" : "psu_ocm_1", "type" : "memory", "base_addr" : [0xfffd0000]},
	"NODE_OCM_BANK_2" : { "label" : "NODE_OCM_BANK_2", "periph" : "psu_ocm_2", "type" : "memory", "base_addr" : [0xfffe0000]},
	"NODE_OCM_BANK_3" : { "label" : "NODE_OCM_BANK_3", "periph" : "psu_ocm_3", "type" : "memory", "base_addr" : [0xffff0000]},
	"NODE_TCM_0_A" : { "label" : "NODE_TCM_0_A", "periph" : "psu_r5_0_atcm_global", "type" : "memory", "base_addr" : [0xffe00000] },
	"NODE_TCM_0_B" : { "label" : "NODE_TCM_0_B", "periph" : "psu_r5_0_btcm_global", "type" : "memory", "base_addr" : [0xffe20000] },
	"NODE_TCM_1_A" : { "label" : "NODE_TCM_1_A", "periph" : "psu_r5_1_atcm_global", "type" : "memory", "base_addr" : [0xffe90000] },
	"NODE_TCM_1_B" : { "label" : "NODE_TCM_1_B", "periph" : "psu_r5_1_btcm_global", "type" : "memory", "base_addr" : [0xffeb0000] },
	"NODE_L2" : { "label" : "NODE_L2", "periph" : "NA", "type" : "others" },
	"NODE_GPU_PP_0" : { "label" : "NODE_GPU_PP_0", "periph" : "psu_gpu", "type" : "slave", "base_addr" : [0xfd4b0000] },
	"NODE_GPU_PP_1" : { "label" : "NODE_GPU_PP_1", "periph" : "psu_gpu", "type" : "slave", "base_addr" : [0xfd4b0000] },
	"NODE_USB_0" : { "label" : "NODE_USB_0", "periph" : "psu_usb_0", "type" : "slave", "base_addr" : [0xff9d0000] },
	"NODE_USB_1" : { "label" : "NODE_USB_1", "periph" : "psu_usb_1", "type" : "slave", "base_addr" : [0xff9e0000] },
	"NODE_TTC_0" : { "label" : "NODE_TTC_0", "periph" : "psu_ttc_0", "type" : "slave", "base_addr" : [0xff110000] },
	"NODE_TTC_1" : { "label" : "NODE_TTC_1", "periph" : "psu_ttc_1", "type" : "slave", "base_addr" : [0xff120000] },
	"NODE_TTC_2" : { "label" : "NODE_TTC_2", "periph" : "psu_ttc_2", "type" : "slave", "base_addr" : [0xff130000 ] },
	"NODE_TTC_3" : { "label" : "NODE_TTC_3", "periph" : "psu_ttc_3", "type" : "slave", "base_addr" : [0xff140000] },
	"NODE_SATA" : { "label" : "NODE_SATA", "periph" : "psu_sata", "type" : "slave", "base_addr" : [0xfd0c0000] },
	"NODE_ETH_0" : { "label" : "NODE_ETH_0", "periph" : "psu_ethernet_0", "type" : "slave", "base_addr" : [0xff0b0000] },
	"NODE_ETH_1" : { "label" : "NODE_ETH_1", "periph" : "psu_ethernet_1", "type" : "slave", "base_addr" : [0xff0c0000] },
	"NODE_ETH_2" : { "label" : "NODE_ETH_2", "periph" : "psu_ethernet_2", "type" : "slave", "base_addr" : [0xff0d0000] },
	"NODE_ETH_3" : { "label" : "NODE_ETH_3", "periph" : "psu_ethernet_3", "type" : "slave", "base_addr" : [0xff0e0000] },
	"NODE_UART_0" : { "label" : "NODE_UART_0", "periph" : "psu_uart_0", "type" : "slave", "base_addr" : [0xff000000] },
	"NODE_UART_1" : { "label" : "NODE_UART_1", "periph" : "psu_uart_1", "type" : "slave", "base_addr" : [0xff010000] },
	"NODE_SPI_0" : { "label" : "NODE_SPI_0", "periph" : "psu_spi_0", "type" : "slave", "base_addr" : [0xff040000] },
	"NODE_SPI_1" : { "label" : "NODE_SPI_1", "periph" : "psu_spi_1", "type" : "slave", "base_addr" : [0xff050000] },
	"NODE_I2C_0" : { "label" : "NODE_I2C_0", "periph" : "psu_i2c_0", "type" : "slave", "base_addr" : [0xff020000] },
	"NODE_I2C_1" : { "label" : "NODE_I2C_1", "periph" : "psu_i2c_1", "type" : "slave", "base_addr" : [0xff030000] },
	"NODE_SD_0" : { "label" : "NODE_SD_0", "periph" : "psu_sd_0", "type" : "slave", "base_addr" : [0xff160000] },
	"NODE_SD_1" : { "label" : "NODE_SD_1", "periph" : "psu_sd_1", "type" : "slave", "base_addr" : [0xff170000] },
	"NODE_DP" : { "label" : "NODE_DP", "periph" : "psu_dp", "type" : "slave", "base_addr" : [0xfd4a0000] },
	"NODE_GDMA" : { "label" : "NODE_GDMA", "periph" : "psu_gdma_0", "type" : "slave", "base_addr" : [0xfd500000] },
	"NODE_ADMA" : { "label" : "NODE_ADMA", "periph" : "psu_adma_0", "type" : "slave", "base_addr" : [0xffa80000] },
	"NODE_NAND" : { "label" : "NODE_NAND", "periph" : "psu_nand_0", "type" : "slave", "base_addr" : [0xff100000] },
	"NODE_QSPI" : { "label" : "NODE_QSPI", "periph" : "psu_qspi_0", "type" : "slave", "base_addr" : [0xff0f0000] },
	"NODE_GPIO" : { "label" : "NODE_GPIO", "periph" : "psu_gpio_0", "type" : "slave", "base_addr" : [0xff0a0000] },
	"NODE_CAN_0" : { "label" : "NODE_CAN_0", "periph" : "psu_can_0", "type" : "slave", "base_addr" : [0xff060000] },
	"NODE_CAN_1" : { "label" : "NODE_CAN_1", "periph" : "psu_can_1", "type" : "slave", "base_addr" : [0xff070000] },
	"NODE_EXTERN" : { "label" : "NODE_EXTERN", "periph" : "NA", "type" : "others" },
	"NODE_DDR" : { "label" : "NODE_DDR", "periph" : "psu_ddr", "type" : "memory", "base_addr" : [0x100000,0x0] },
	"NODE_IPI_APU" : { "label" : "NODE_IPI_APU", "periph" : "NA", "type" : "ipi" },
	"NODE_IPI_RPU_0" : { "label" : "NODE_IPI_RPU_0", "periph" : "NA", "type" : "ipi" },
	"NODE_IPI_RPU_1" : { "label" : "NODE_IPI_RPU_1", "periph" : "NA", "type" : "ipi" },
	"NODE_GPU" : { "label" : "NODE_GPU", "periph" : "psu_gpu", "type" : "slave", "base_addr" : [0xfd4b0000] },
	"NODE_PCIE" : { "label" : "NODE_PCIE", "periph" : "psu_pcie", "type" : "slave", "base_addr" : [0xfd0e0000] },
	"NODE_PCAP" : { "label" : "NODE_PCAP", "periph" : "NA", "type" : "slave" },
	"NODE_RTC" : { "label" : "NODE_RTC", "periph" : "psu_rtc", "type" : "slave", "base_addr" : [0xffa60000] },
	"NODE_VCU" : { "label" : "NODE_VCU", "periph" : "vcu_0", "type" : "slave" },
	"NODE_PL" : { "label" : "NODE_PL", "periph" : "NA", "type" : "others" },
}

masters = {
               "psu_cortexa53_0" : {'name' : 'APU'},
               "psu_cortexr5_0" : {'name' : 'RPU0'},
               "psu_cortexr5_1" : {'name' : 'RPU1'}
          }

ocm_map = {
    "psu_ocm_0" : { "label" : "OCM_BANK_0", "base" : 0xFFFC0000, "high" : 0xFFFCFFFF },
    "psu_ocm_1" : { "label" : "OCM_BANK_1", "base" : 0xFFFD0000, "high" : 0xFFFDFFFF },
    "psu_ocm_2" : { "label" : "OCM_BANK_2", "base" : 0xFFFE0000, "high" : 0xFFFEFFFF },
    "psu_ocm_3" : { "label" : "OCM_BANK_3", "base" : 0xFFFF0000, "high" : 0xFFFFFFFF }
}

apu_prealloc_list = [
                        "NODE_DDR",
                        "NODE_L2",
                        "NODE_OCM_BANK_0",
                        "NODE_OCM_BANK_1",
                        "NODE_OCM_BANK_2",
                        "NODE_OCM_BANK_3",
                        "NODE_I2C_0",
                        "NODE_I2C_1",
                        "NODE_SD_1",
                        "NODE_QSPI",
                        "NODE_PL"
                    ]

rpu_0_prealloc_list = [
                        "NODE_TCM_0_A",
                        "NODE_TCM_0_B",
                        "NODE_TCM_1_A",
                        "NODE_TCM_1_B"
                     ]

rpu_0_prealloc_conditional_list = [
		        "NODE_DDR",
                        "NODE_OCM_BANK_0",
                        "NODE_OCM_BANK_1",
                        "NODE_OCM_BANK_2",
                        "NODE_OCM_BANK_3",
                        "NODE_I2C_0",
                        "NODE_I2C_1",
                        "NODE_SD_1",
                        "NODE_QSPI",
                        "NODE_PL",
                        "NODE_ADMA"
	            ]

rpu_1_prealloc_list = [
                        "NODE_TCM_1_A",
                        "NODE_TCM_1_B"
                     ]

hardcoded_proc_type = "psu_cortexa53_0"

power_node_list = [
                    "NODE_APU",
                    "NODE_RPU",
                    "NODE_FPD",
                    "NODE_PLD"
                  ]

power_perms = {
                "NODE_APU" : [ "psu_cortexr5_0", "psu_cortexr5_1" ],
                "NODE_RPU" : [ "psu_cortexa53_0", "psu_cortexr5_0", "psu_cortexr5_1" ],
                "NODE_FPD" : [ "psu_cortexr5_0", "psu_cortexr5_1" ],
                "NODE_PLD" : [ "psu_cortexa53_0", "psu_cortexr5_0", "psu_cortexr5_1" ],
}

reset_line_map = {
	"XILPM_RESET_PCIE_CFG" : { "label" : "XILPM_RESET_PCIE_CFG",  "type" : "rst_periph",  "node" : "NODE_PCIE" },
	"XILPM_RESET_PCIE_BRIDGE" : { "label" : "XILPM_RESET_PCIE_BRIDGE",  "type" : "rst_periph",  "node" : "NODE_PCIE" },
	"XILPM_RESET_PCIE_CTRL" : { "label" : "XILPM_RESET_PCIE_CTRL",  "type" : "rst_periph",  "node" : "NODE_PCIE" },
	"XILPM_RESET_DP" : { "label" : "XILPM_RESET_DP",  "type" : "rst_periph",  "node" : "NODE_DP" },
	"XILPM_RESET_SWDT_CRF" : { "label" : "XILPM_RESET_SWDT_CRF",  "type" : "normal" },
	"XILPM_RESET_AFI_FM5" : { "label" : "XILPM_RESET_AFI_FM5",  "type" : "normal" },
	"XILPM_RESET_AFI_FM4" : { "label" : "XILPM_RESET_AFI_FM4",  "type" : "normal" },
	"XILPM_RESET_AFI_FM3" : { "label" : "XILPM_RESET_AFI_FM3",  "type" : "normal" },
	"XILPM_RESET_AFI_FM2" : { "label" : "XILPM_RESET_AFI_FM2",  "type" : "normal" },
	"XILPM_RESET_AFI_FM1" : { "label" : "XILPM_RESET_AFI_FM1",  "type" : "normal" },
	"XILPM_RESET_AFI_FM0" : { "label" : "XILPM_RESET_AFI_FM0",  "type" : "normal" },
	"XILPM_RESET_GDMA" : { "label" : "XILPM_RESET_GDMA",  "type" : "rst_periph",  "node" : "NODE_GDMA" },
	"XILPM_RESET_GPU_PP1" : { "label" : "XILPM_RESET_GPU_PP1",  "type" : "rst_periph",  "node" : "NODE_GPU_PP_1" },
	"XILPM_RESET_GPU_PP0" : { "label" : "XILPM_RESET_GPU_PP0",  "type" : "rst_periph",  "node" : "NODE_GPU_PP_0" },
	"XILPM_RESET_GPU" : { "label" : "XILPM_RESET_GPU",  "type" : "rst_periph",  "node" : "NODE_GPU" },
	"XILPM_RESET_GT" : { "label" : "XILPM_RESET_GT",  "type" : "normal" },
	"XILPM_RESET_SATA" : { "label" : "XILPM_RESET_SATA",  "type" : "rst_periph",  "node" : "NODE_SATA" },
	"XILPM_RESET_ACPU3_PWRON" : { "label" : "XILPM_RESET_ACPU3_PWRON",  "type" : "rst_proc", "proc" : "APU" },
	"XILPM_RESET_ACPU2_PWRON" : { "label" : "XILPM_RESET_ACPU2_PWRON",  "type" : "rst_proc", "proc" : "APU" },
	"XILPM_RESET_ACPU1_PWRON" : { "label" : "XILPM_RESET_ACPU1_PWRON",  "type" : "rst_proc", "proc" : "APU" },
	"XILPM_RESET_ACPU0_PWRON" : { "label" : "XILPM_RESET_ACPU0_PWRON",  "type" : "rst_proc", "proc" : "APU" },
	"XILPM_RESET_APU_L2" : { "label" : "XILPM_RESET_APU_L2",  "type" : "rst_proc", "proc" : "APU" },
	"XILPM_RESET_ACPU3" : { "label" : "XILPM_RESET_ACPU3",  "type" : "rst_proc", "proc" : "APU" },
	"XILPM_RESET_ACPU2" : { "label" : "XILPM_RESET_ACPU2",  "type" : "rst_proc", "proc" : "APU" },
	"XILPM_RESET_ACPU1" : { "label" : "XILPM_RESET_ACPU1",  "type" : "rst_proc", "proc" : "APU" },
	"XILPM_RESET_ACPU0" : { "label" : "XILPM_RESET_ACPU0",  "type" : "rst_proc", "proc" : "APU" },
	"XILPM_RESET_DDR" : { "label" : "XILPM_RESET_DDR",  "type" : "rst_periph",  "node" : "NODE_DDR" },
	"XILPM_RESET_APM_FPD" : { "label" : "XILPM_RESET_APM_FPD",  "type" : "normal" },
	"XILPM_RESET_SOFT" : { "label" : "XILPM_RESET_SOFT",  "type" : "rst_shared" },
	"XILPM_RESET_GEM0" : { "label" : "XILPM_RESET_GEM0",  "type" : "rst_periph",  "node" : "NODE_ETH_0" },
	"XILPM_RESET_GEM1" : { "label" : "XILPM_RESET_GEM1",  "type" : "rst_periph",  "node" : "NODE_ETH_1" },
	"XILPM_RESET_GEM2" : { "label" : "XILPM_RESET_GEM2",  "type" : "rst_periph",  "node" : "NODE_ETH_2" },
	"XILPM_RESET_GEM3" : { "label" : "XILPM_RESET_GEM3",  "type" : "rst_periph",  "node" : "NODE_ETH_3" },
	"XILPM_RESET_QSPI" : { "label" : "XILPM_RESET_QSPI",  "type" : "rst_periph",  "node" : "NODE_QSPI" },
	"XILPM_RESET_UART0" : { "label" : "XILPM_RESET_UART0",  "type" : "rst_periph",  "node" : "NODE_UART_0" },
	"XILPM_RESET_UART1" : { "label" : "XILPM_RESET_UART1",  "type" : "rst_periph",  "node" : "NODE_UART_1" },
	"XILPM_RESET_SPI0" : { "label" : "XILPM_RESET_SPI0",  "type" : "rst_periph",  "node" : "NODE_SPI_0" },
	"XILPM_RESET_SPI1" : { "label" : "XILPM_RESET_SPI1",  "type" : "rst_periph",  "node" : "NODE_SPI_1" },
	"XILPM_RESET_SDIO0" : { "label" : "XILPM_RESET_SDIO0",  "type" : "normal" },
	"XILPM_RESET_SDIO1" : { "label" : "XILPM_RESET_SDIO1",  "type" : "normal" },
	"XILPM_RESET_CAN0" : { "label" : "XILPM_RESET_CAN0",  "type" : "rst_periph",  "node" : "NODE_CAN_0" },
	"XILPM_RESET_CAN1" : { "label" : "XILPM_RESET_CAN1",  "type" : "rst_periph",  "node" : "NODE_CAN_1" },
	"XILPM_RESET_I2C0" : { "label" : "XILPM_RESET_I2C0",  "type" : "rst_periph",  "node" : "NODE_I2C_0" },
	"XILPM_RESET_I2C1" : { "label" : "XILPM_RESET_I2C1",  "type" : "rst_periph",  "node" : "NODE_I2C_1" },
	"XILPM_RESET_TTC0" : { "label" : "XILPM_RESET_TTC0",  "type" : "rst_periph",  "node" : "NODE_TTC_0" },
	"XILPM_RESET_TTC1" : { "label" : "XILPM_RESET_TTC1",  "type" : "rst_periph",  "node" : "NODE_TTC_1" },
	"XILPM_RESET_TTC2" : { "label" : "XILPM_RESET_TTC2",  "type" : "rst_periph",  "node" : "NODE_TTC_2" },
	"XILPM_RESET_TTC3" : { "label" : "XILPM_RESET_TTC3",  "type" : "rst_periph",  "node" : "NODE_TTC_3" },
	"XILPM_RESET_SWDT_CRL" : { "label" : "XILPM_RESET_SWDT_CRL",  "type" : "normal" },
	"XILPM_RESET_NAND" : { "label" : "XILPM_RESET_NAND",  "type" : "rst_periph",  "node" : "NODE_NAND" },
	"XILPM_RESET_ADMA" : { "label" : "XILPM_RESET_ADMA",  "type" : "rst_periph",  "node" : "NODE_ADMA" },
	"XILPM_RESET_GPIO" : { "label" : "XILPM_RESET_GPIO",  "type" : "normal" },
	"XILPM_RESET_IOU_CC" : { "label" : "XILPM_RESET_IOU_CC",  "type" : "normal" },
	"XILPM_RESET_TIMESTAMP" : { "label" : "XILPM_RESET_TIMESTAMP",  "type" : "normal" },
	"XILPM_RESET_RPU_R50" : { "label" : "XILPM_RESET_RPU_R50",  "type" : "rst_proc", "proc" : "RPU_0" },
	"XILPM_RESET_RPU_R51" : { "label" : "XILPM_RESET_RPU_R51",  "type" : "rst_proc", "proc" : "RPU_1" },
	"XILPM_RESET_RPU_AMBA" : { "label" : "XILPM_RESET_RPU_AMBA",  "type" : "rst_proc", "proc" : "RPU" },
	"XILPM_RESET_OCM" : { "label" : "XILPM_RESET_OCM",  "type" : "rst_periph",  "node" : "NODE_OCM_BANK_0" },
	"XILPM_RESET_RPU_PGE" : { "label" : "XILPM_RESET_RPU_PGE",  "type" : "rst_proc", "proc" : "RPU" },
	"XILPM_RESET_USB0_CORERESET" : { "label" : "XILPM_RESET_USB0_CORERESET",  "type" : "rst_periph",  "node" : "NODE_USB_0" },
	"XILPM_RESET_USB1_CORERESET" : { "label" : "XILPM_RESET_USB1_CORERESET",  "type" : "rst_periph",  "node" : "NODE_USB_1" },
	"XILPM_RESET_USB0_HIBERRESET" : { "label" : "XILPM_RESET_USB0_HIBERRESET",  "type" : "rst_periph",  "node" : "NODE_USB_0" },
	"XILPM_RESET_USB1_HIBERRESET" : { "label" : "XILPM_RESET_USB1_HIBERRESET",  "type" : "rst_periph",  "node" : "NODE_USB_1" },
	"XILPM_RESET_USB0_APB" : { "label" : "XILPM_RESET_USB0_APB",  "type" : "rst_periph",  "node" : "NODE_USB_0" },
	"XILPM_RESET_USB1_APB" : { "label" : "XILPM_RESET_USB1_APB",  "type" : "rst_periph",  "node" : "NODE_USB_1" },
	"XILPM_RESET_IPI" : { "label" : "XILPM_RESET_IPI",  "type" : "rst_shared" },
	"XILPM_RESET_APM_LPD" : { "label" : "XILPM_RESET_APM_LPD",  "type" : "normal" },
	"XILPM_RESET_RTC" : { "label" : "XILPM_RESET_RTC",  "type" : "rst_periph",  "node" : "NODE_RTC" },
	"XILPM_RESET_SYSMON" : { "label" : "XILPM_RESET_SYSMON",  "type" : "NA" },
	"XILPM_RESET_AFI_FM6" : { "label" : "XILPM_RESET_AFI_FM6",  "type" : "normal" },
	"XILPM_RESET_LPD_SWDT" : { "label" : "XILPM_RESET_LPD_SWDT",  "type" : "normal" },
	"XILPM_RESET_FPD" : { "label" : "XILPM_RESET_FPD",  "type" : "rpu_only" },
	"XILPM_RESET_RPU_DBG1" : { "label" : "XILPM_RESET_RPU_DBG1",  "type" : "rst_proc", "proc" : "RPU" },
	"XILPM_RESET_RPU_DBG0" : { "label" : "XILPM_RESET_RPU_DBG0",  "type" : "rst_proc", "proc" : "RPU" },
	"XILPM_RESET_DBG_LPD" : { "label" : "XILPM_RESET_DBG_LPD",  "type" : "normal" },
	"XILPM_RESET_DBG_FPD" : { "label" : "XILPM_RESET_DBG_FPD",  "type" : "normal" },
	"XILPM_RESET_APLL" : { "label" : "XILPM_RESET_APLL",  "type" : "rst_proc", "proc" : "APU" },
	"XILPM_RESET_DPLL" : { "label" : "XILPM_RESET_DPLL",  "type" : "rst_shared" },
	"XILPM_RESET_VPLL" : { "label" : "XILPM_RESET_VPLL",  "type" : "rst_shared" },
	"XILPM_RESET_IOPLL" : { "label" : "XILPM_RESET_IOPLL",  "type" : "rst_shared" },
	"XILPM_RESET_RPLL" : { "label" : "XILPM_RESET_RPLL",  "type" : "rst_proc", "proc" : "RPU" },
	"XILPM_RESET_GPO3_PL_0" : { "label" : "XILPM_RESET_GPO3_PL_0",  "type" : "normal" },
	"XILPM_RESET_GPO3_PL_1" : { "label" : "XILPM_RESET_GPO3_PL_1",  "type" : "normal" },
	"XILPM_RESET_GPO3_PL_2" : { "label" : "XILPM_RESET_GPO3_PL_2",  "type" : "normal" },
	"XILPM_RESET_GPO3_PL_3" : { "label" : "XILPM_RESET_GPO3_PL_3",  "type" : "normal" },
	"XILPM_RESET_GPO3_PL_4" : { "label" : "XILPM_RESET_GPO3_PL_4",  "type" : "normal" },
	"XILPM_RESET_GPO3_PL_5" : { "label" : "XILPM_RESET_GPO3_PL_5",  "type" : "normal" },
	"XILPM_RESET_GPO3_PL_6" : { "label" : "XILPM_RESET_GPO3_PL_6",  "type" : "normal" },
	"XILPM_RESET_GPO3_PL_7" : { "label" : "XILPM_RESET_GPO3_PL_7",  "type" : "normal" },
	"XILPM_RESET_GPO3_PL_8" : { "label" : "XILPM_RESET_GPO3_PL_8",  "type" : "normal" },
	"XILPM_RESET_GPO3_PL_9" : { "label" : "XILPM_RESET_GPO3_PL_9",  "type" : "normal" },
	"XILPM_RESET_GPO3_PL_10" : { "label" : "XILPM_RESET_GPO3_PL_10",  "type" : "normal" },
	"XILPM_RESET_GPO3_PL_11" : { "label" : "XILPM_RESET_GPO3_PL_11",  "type" : "normal" },
	"XILPM_RESET_GPO3_PL_12" : { "label" : "XILPM_RESET_GPO3_PL_12",  "type" : "normal" },
	"XILPM_RESET_GPO3_PL_13" : { "label" : "XILPM_RESET_GPO3_PL_13",  "type" : "normal" },
	"XILPM_RESET_GPO3_PL_14" : { "label" : "XILPM_RESET_GPO3_PL_14",  "type" : "normal" },
	"XILPM_RESET_GPO3_PL_15" : { "label" : "XILPM_RESET_GPO3_PL_15",  "type" : "normal" },
	"XILPM_RESET_GPO3_PL_16" : { "label" : "XILPM_RESET_GPO3_PL_16",  "type" : "normal" },
	"XILPM_RESET_GPO3_PL_17" : { "label" : "XILPM_RESET_GPO3_PL_17",  "type" : "normal" },
	"XILPM_RESET_GPO3_PL_18" : { "label" : "XILPM_RESET_GPO3_PL_18",  "type" : "normal" },
	"XILPM_RESET_GPO3_PL_19" : { "label" : "XILPM_RESET_GPO3_PL_19",  "type" : "normal" },
	"XILPM_RESET_GPO3_PL_20" : { "label" : "XILPM_RESET_GPO3_PL_20",  "type" : "normal" },
	"XILPM_RESET_GPO3_PL_21" : { "label" : "XILPM_RESET_GPO3_PL_21",  "type" : "normal" },
	"XILPM_RESET_GPO3_PL_22" : { "label" : "XILPM_RESET_GPO3_PL_22",  "type" : "normal" },
	"XILPM_RESET_GPO3_PL_23" : { "label" : "XILPM_RESET_GPO3_PL_23",  "type" : "normal" },
	"XILPM_RESET_GPO3_PL_24" : { "label" : "XILPM_RESET_GPO3_PL_24",  "type" : "normal" },
	"XILPM_RESET_GPO3_PL_25" : { "label" : "XILPM_RESET_GPO3_PL_25",  "type" : "normal" },
	"XILPM_RESET_GPO3_PL_26" : { "label" : "XILPM_RESET_GPO3_PL_26",  "type" : "normal" },
	"XILPM_RESET_GPO3_PL_27" : { "label" : "XILPM_RESET_GPO3_PL_27",  "type" : "normal" },
	"XILPM_RESET_GPO3_PL_28" : { "label" : "XILPM_RESET_GPO3_PL_28",  "type" : "normal" },
	"XILPM_RESET_GPO3_PL_29" : { "label" : "XILPM_RESET_GPO3_PL_29",  "type" : "normal" },
	"XILPM_RESET_GPO3_PL_30" : { "label" : "XILPM_RESET_GPO3_PL_30",  "type" : "normal" },
	"XILPM_RESET_GPO3_PL_31" : { "label" : "XILPM_RESET_GPO3_PL_31",  "type" : "normal" },
	"XILPM_RESET_RPU_LS" : { "label" : "XILPM_RESET_RPU_LS",  "type" : "rst_proc", "proc" : "RPU" },
	"XILPM_RESET_PS_ONLY" : { "label" : "XILPM_RESET_PS_ONLY",  "type" : "normal" },
	"XILPM_RESET_PL" : { "label" : "XILPM_RESET_PL",  "type" : "normal" },
	"XILPM_RESET_GPIO5_EMIO_92" : { "label" : "XILPM_RESET_GPIO5_EMIO_92",  "type" : "normal" },
	"XILPM_RESET_GPIO5_EMIO_93" : { "label" : "XILPM_RESET_GPIO5_EMIO_93",  "type" : "normal" },
	"XILPM_RESET_GPIO5_EMIO_94" : { "label" : "XILPM_RESET_GPIO5_EMIO_94",  "type" : "normal" },
	"XILPM_RESET_GPIO5_EMIO_95" : { "label" : "XILPM_RESET_GPIO5_EMIO_95",  "type" : "normal" },
}

gpo_nums = [2,3,4,5]

