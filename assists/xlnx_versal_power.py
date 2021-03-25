from enum import IntEnum

class REQ_USAGE(IntEnum):
  REQ_NO_RESTRICTION = 0
  REQ_SHARED = 1
  REQ_NONSHARED = 2
  REQ_TIME_SHARED = 3

# if this bit combination is on for usage offset, the meaning is as described below
req_usage_message = "Device usage policies"
req_usage = {
 REQ_USAGE.REQ_NO_RESTRICTION : "device accessible from all subsystem",
 REQ_USAGE.REQ_SHARED : "device simultaneously shared between two or more subsystems",
 REQ_USAGE.REQ_NONSHARED : "device exclusively reserved by one subsystem, always",
 REQ_USAGE.REQ_TIME_SHARED : "device is time shared between two or more subsystems",
}

usage_mask = 0x3
def usage(flags):
  msg = "#    usage: "
  msg += req_usage[flags & usage_mask]
  return msg

class REGION_SECURITY(IntEnum):
  ACCESS_FROM_SECURE = 0
  ACCESS_FROM_NONSECURE = 1

req_security_message = "Device/Memory region security status requirement per TrustZone."
req_security = {
 REGION_SECURITY.ACCESS_FROM_SECURE : "Device/Memory region only allows access from secure masters",
 REGION_SECURITY.ACCESS_FROM_NONSECURE : "Device/Memory region allow both secure or non-secure masters",
}
security_mask = 0x4
security_offset = 0x2
def security(flags):
  msg = "#    security: "
  msg += req_security[(flags & security_mask) >> security_offset]
  return msg

class RDWR_POLICY(IntEnum):
  ALLOWED = 0
  NOT_ALLOWED = 1

# this map is only applicable for memory regions
req_rd_wr_message = "Read/Write access control policy"
req_rd_wr = {
  RDWR_POLICY.ALLOWED : "Transaction allowed",
  RDWR_POLICY.NOT_ALLOWED : "Transaction not Allowed",
}
rd_policy_mask = 0x8
rd_policy_offset = 0x3
wr_policy_mask = 0x10
wr_policy_offset = 0x4
rw_message = "Read/Write access control policy."
def read_policy(flags):
  msg = "#    read policy: "
  msg += req_rd_wr[(flags & rd_policy_mask) >> rd_policy_offset]
  return msg

def write_policy(flags):
  msg = "#    write policy: "
  msg += req_rd_wr[(flags & wr_policy_mask) >> wr_policy_offset]
  return msg


nsregn_check_mask = 0x20
nsregn_check_offset = 0x5

class NSREGN_POLICY(IntEnum):
  RELAXED = 0
  STRICT = 1

nsregn_message = "Non-secure memory region check type policy."
nsregn = {
  NSREGN_POLICY.RELAXED : "RELAXED",
  NSREGN_POLICY.STRICT: "STRICT",
}

def nsregn_policy(flags):
  msg = "#    Non-secure memory region check type policy: "
  msg += nsregn[(flags & nsregn_check_mask) >> nsregn_check_offset]
  return msg

capability_offset = 0x8
capability_mask = 0x7F00

cap_message = "capability: "
def capability_policy(flags):
  msg = "#    Capability policy: "
  msg += hex((flags & capability_mask) >> capability_offset)
  return msg

prealloc_offset = 0xf
prealloc_mask = 0x8000

class PREALLOC(IntEnum):
  NOT_REQUIRED = 0
  REQUIRED  = 1

prealloc = {
  PREALLOC.NOT_REQUIRED : "prealloc not required",
  PREALLOC.REQUIRED : "prealloc required",
}

prealloc_message = "prealloc policy "
def prealloc_policy(flags):
  msg = "#    Preallocation policy: "
  msg += prealloc[(flags & prealloc_mask) >> prealloc_offset]
  return msg

class Requirement:
  def __init__(self, subsystem, node, prealloc, capability, nsregn_policy,
               read_policy, write_policy, security, usage):
    self.prealloc       = prealloc
    self.capability     = capability
    self.nsregn_policy  = nsregn_policy
    self.read_policy    = read_policy
    self.write_policy   = write_policy
    self.security       = security
    self.usage          = usage
    self.subsystem     = subsystem
    self.node           = node


mailbox_devices = {
  "mailbox@ff320000":"dev_ipi_0",
  "mailbox@ff390000":"dev_ipi_1",
  "mailbox@ff310000":"dev_ipi_2",
  "mailbox@ff330000":"dev_ipi_3",
  "mailbox@ff340000":"dev_ipi_4",
  "mailbox@ff350000":"dev_ipi_5",
  "mailbox@ff360000":"dev_ipi_6",
}

apu_specific_reqs = {
  "dev_l2_bank_0":  0x4,
  "dev_ams_root":   0x4,
  "dev_acpu_0":     0x8104,
  "dev_acpu_1":     0x8104,
}

cpu_subsystem_map = {
  "a72" :       0x1c000003,
  "r5_lockstep":0x1c000004,
  "r5_0":       0x1c000005,
  "r5_1":       0x1c000006,
}

memory_range_to_dev_name = {
 0xffe00000:"dev_tcm_0_a",
 0xffe20000:"dev_tcm_0_b",
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
