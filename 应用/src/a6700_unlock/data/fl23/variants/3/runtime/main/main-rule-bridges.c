typedef unsigned int u32;typedef unsigned long long u64;
struct node {u64 next;u32 key,value;};
struct header {const u64 *cases;u32 target,source;};
static const struct node additional[2]={{0,17,24802U},{0,18,24803U}};
static struct {u64 lookup,action,map;const u64 *headers;u32 count;u32 cookie;} binding __attribute__((used))={0,0,0,0,0,0x31524c46};
int fl23_rules_bind(u64 lookup,u64 action,u64 map,const u64 *headers,u32 count) {
 if(!lookup||!action||!map||!headers||(lookup&3)||(action&3)||((u64)headers&7)||!count||count>32)return 1;
 binding.lookup=lookup;binding.action=action;binding.map=map;binding.headers=headers;binding.count=count;return 0;
}
u64 fl23_rule_enum_lookup(u64 map,const u32 *value) {
 if(map==binding.map&&value&&(*value==17||*value==18))return (u64)&additional[*value-17];
 return binding.lookup?((u64(*)(u64,const u32*))binding.lookup)(map,value):0;
}
u64 fl23_rule_action(const struct header *h,const u32 *value) {
 if(h&&value&&h->source==0x4f5&&(*value==24802U||*value==24803U)) {
  for(u32 i=0;i<binding.count;i++)if(binding.headers[i]==(u64)h)return h->cases[18+*value-24802U];
  return 0;
 }
 return binding.action?((u64(*)(const struct header*,const u32*))binding.action)(h,value):0;
}
