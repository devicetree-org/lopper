# TODO ensure mapping is consistent and for correct for each core
mailbox_devices = {
  "mailbox@ff320000":"dev_ipi_0",
  "mailbox@ff390000":"dev_ipi_1",
  "mailbox@ff310000":"dev_ipi_2",
  "mailbox@ff330000":"dev_ipi_3",
  "mailbox@ff340000":"dev_ipi_4",
  "mailbox@ff350000":"dev_ipi_5",
  "mailbox@ff360000":"dev_ipi_6",
}

cpu_subsystem_map = {
  "a72" :       0x1c000003,
  "r5_lockstep":0x1c000004,
  "r5_0":       0x1c000005,
  "r5_1":       0x1c000006,
}

memory_range_to_dev_name = {
 0xffe00000:"dev_tcm_0_a",
 0xffe20000:"dev_tcm_0_a",
 0xffe90000:"dev_tcm_1_a",
 0xffeb0000:"dev_tcm_1_b",
 0x0:"dev_ddr_0",
}

ocm_bank_names = [
  "dev_ocm_bank_0",
  "dev_ocm_bank_1",
  "dev_ocm_bank_2",
  "dev_ocm_bank_3"
]

existing_devices = {
  "dev_rpu0_0":0x18110005 ,
  "dev_rpu0_1":0x18110006 ,
  "dev_ddr_0":0x18320010 ,
  "dev_ocm_bank_0":0x18314007 ,
  "dev_ocm_bank_1":0x18314008 ,
  "dev_ocm_bank_2":0x18314009 ,
  "dev_ocm_bank_3":0x1831400a ,
  "dev_tcm_0_a":0x1831800b ,
  "dev_tcm_0_b":0x1831800c ,
  "dev_tcm_1_a":0x1831800d ,
  "dev_tcm_1_b":0x1831800e ,

  "dev_acpu_0": 0x1810c003,
  "dev_acpu_1" : 0x1810c004 ,
  "dev_ipi_0":0x1822403d ,
  "dev_ipi_1":0x1822403e ,
  "dev_ipi_2":0x1822403f ,
  "dev_ipi_3":0x18224040 ,
  "dev_ipi_4":0x18224041 ,
  "dev_ipi_5":0x18224042 ,
  "dev_ipi_6":0x18224043 ,
  "dev_l2_bank_0": 0x1831c00f,
  "dev_ams_root": 0x18224055,
}

# map xilpm IDs to strings
device_lookup = { 0x1831c00f : "dev_l2_bank_0" ,
  0x18224055 : "dev_ams_root"
}

xilinx_versal_device_names = {
	0x18224018	:   "PM_DEV_USB_0"		,
	0x18224019	:   "PM_DEV_GEM_0"		,
	0x1822401a	:   "PM_DEV_GEM_1"		,
	0x1822401b	:   "PM_DEV_SPI_0"		,
	0x1822401c	:   "PM_DEV_SPI_1"		,
	0x1822401d	:   "PM_DEV_I2C_0"		,
	0x1822401e	:   "PM_DEV_I2C_1"		,
	0x1822401f	:   "PM_DEV_CAN_FD_0"	,
	0x18224020	:   "PM_DEV_CAN_FD_1"	,
	0x18224021	:   "PM_DEV_UART_0"	,	
	0x18224022	:   "PM_DEV_UART_1"	,	
	0x18224023	:   "PM_DEV_GPIO"		,
	0x18224024	:   "PM_DEV_TTC_0"		,
	0x18224025	:   "PM_DEV_TTC_1"		,
	0x18224026	:   "PM_DEV_TTC_2"		,
	0x18224027	:   "PM_DEV_TTC_3"		,
	0x18224029	:   "PM_DEV_SWDT_FPD"	,
	0x1822402a	:   "PM_DEV_OSPI"		,
	0x1822402b	:   "PM_DEV_QSPI"		,
	0x1822402c	:	"PM_DEV_GPIO_PMC"		,
	0x1822402e	:   "PM_DEV_SDIO_0"	,		
	0x1822402f	:   "PM_DEV_SDIO_1"	,		
	0x18224034	:   "PM_DEV_RTC"	,	
	0x18224035	:   "PM_DEV_ADMA_0"	,		
	0x18224036	:   "PM_DEV_ADMA_1"	,		
	0x18224037	:   "PM_DEV_ADMA_2"	,		
	0x18224038	:   "PM_DEV_ADMA_3"	,		
	0x18224039	:   "PM_DEV_ADMA_4"	,		
	0x1822403a	:   "PM_DEV_ADMA_5"	,		
	0x1822403b	:   "PM_DEV_ADMA_6"	,		
	0x1822403c	:   "PM_DEV_ADMA_7"	,		
	0x18224072	:   "PM_DEV_AI"	
}
