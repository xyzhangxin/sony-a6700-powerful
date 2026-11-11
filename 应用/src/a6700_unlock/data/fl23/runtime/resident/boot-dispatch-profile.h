#define A1_APP_END 0x5000000ULL
#define A1_SELF_PLT 0x176cf70ULL
#define A1_SELF_GOT 0x4f208b0ULL
#define A1_SELF_OFFSET 0x8440ULL
static const unsigned char self_guard[16]={64,208,59,213,0,192,27,209,192,3,95,214,0,0,0,0};
#define A1_CREATE_OFFSET 0x7320ULL
static const unsigned char create_guard[16]={253,123,177,169,253,3,0,145,247,99,3,169,248,3,2,170};
#define A1_DETACH_OFFSET 0x7fc0ULL
static const unsigned char detach_guard[16]={2,208,64,185,162,3,248,55,253,123,190,169,225,3,0,170};
static const unsigned char self_plt_guard[16]={176,189,1,144,17,90,68,249,16,194,34,145,32,2,31,214};
