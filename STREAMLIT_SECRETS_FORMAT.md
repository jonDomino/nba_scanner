# Streamlit Secrets Format - Copy-Paste Instructions

## ✅ CORRECT FORMAT (Use This)

When pasting into Streamlit Cloud → Settings → Secrets, use this format:

```toml
KALSHI_API_KEY_ID = "8a9bc3f3-ba54-4c71-a920-fdb14fc6e295"
KALSHI_PRIVATE_KEY_PEM = """-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEA2g2S6ZppaXiV/T+QxrCzRnZ80No3PxPPUEnSoO4QLh8Wy87o
qjx1dIQZlQzHSSdLWsyBkqDv6EcOxBv4rCLXM6pdzdz/1SpQIn+BFnA8L+xGEjQ7
ZmISxByCPzyiD+TudXZ80mfsJT0LYJAzfzCf/TLd3oFDPWg9+hUU3YObSR07rkOE
NqZHkXg2DDtvjTkNxXzxQWc0t+6JLY9YxOmgqVlArmo2VoFjrhCZAyq2MCByzuK8
hH6E8kDq21co5p8+jytrvnGkjKIs52rS2yOjj5UMmQHAQnfYpjfvajOGdGDUK/VL
yvOl7OOija/6QzbRLyQyOMgCbt/OIJBsI+Ix+wIDAQABAoIBABCMoy/Tplh47HlS
D7zkwFaCu6pmRXcmG7/uOmw4ZUX62hIR3Bq/h/KV7BMh6yYkM1Xq25KzYEHy0q4T
qWX3Jvom3gO32DrngTl9D+ESF59TgWX0TOXSX7Y+q6v9ABfQukB0A7k8ZfHVlj13
9FCc8ieXartueBn7BWkrnG/Lg0ejKn0LvDmppzDGYgAEFxnmAGpCEZ/a9ADyc7f/
0He0xGhqYn4OVpL6TxkddO2/6x9mL4a6WT1FLTTuF6S4iXC59wswKm8AwdM2hpuV
QcOQoqjFzhqrbkKuRqWP9ngLK4mglAMyNHtG7I2pqtNo6qQjmwD/9JxUh8LyA41z
SOMv6VECgYEA6qIJXU10Jxo3wiwfVgb2WS6w8IiiKdWYE1yd8w1jjPLO8BbDIQT8
oiYBvDBdRQzeLYruousuxpe5SXC4CCIxHujP1qqrjQFMW4I3+gz23xoAv7nmbz59
eF+TGkj2UXNmPifOLjIIscDd10qTxGk8U3OZB3lojbCk/oLf2jo1rFUCgYEA7ekD
BCZYek5ojriT9jfevTjlIASab0BLTxbcKyvhRZNmCO1Ahv6B3rEZxX97+ao+GwPU
zzJAfA7ONvCH9ZKJhCiCM9igrxEHswYX6VXtCfiXjJBimky5xHC2iSnNIdEbpZIx
OEjDBg1wg0FFR2Yp4q5RixSLZ5gnvgqnsz1RtQ8CgYAxsGUZTEjX6xZ52Yw9VLYh
BuFT3yuwflp5ZzjP+zrk45rtf3SNbpft4uylJPzrnaEDthN5jyLVzdXgdq2Sk2Mh
r54sUPKJpwe7rWUbYFl7v+7+q2jgkDRUJLFrdJ4te6ngad+hKtlqg3S3nkSS6NJs
veNfkNeSgiE/AQpVFdS+DQKBgB/L76pDUbnu9sx0YNocd3mEvCK+WsGZrzb/0Cc4
8x+wZoe05dXk+AiCVPZvZmtk1G4Z4fxbNAEgnXD8Tr+EeTqfi19QiZE8TnIf01xN
LqQRhHe16GfE72MQyWSloJPvdal4U3m5R89sRmhUdeToA5sXPzC+ay9KR61/kRBE
lsIDAoGBAM2tNUOGHwBhz3lWvS/NM7L6Z1qQ2tvcd/kP59M1Gll3p/pQJHcUtEzC
KeWmJxdpW8kZ+7Jsj1F16OnnGkwENvjiOyJ6ZpaZQc2Hk1rsasdcEpGpAeKegfgk
KLfVgH9he77zNFBXm1YSWtLcyuFmgaua1re+yW6flunCJlVcwlfJ
-----END RSA PRIVATE KEY-----"""
UNABATED_API_KEY = "8c18ccb8a85c4551a0724ddff439b5a5"
```

## ❌ WRONG FORMAT (Don't Use This)

Do NOT include the `[secrets]` section header:

```toml
[secrets]  # ← Remove this line!
KALSHI_API_KEY_ID = "..."
```

## Step-by-Step Instructions

1. **Open Streamlit Cloud Dashboard**
   - Go to your app at [share.streamlit.io](https://share.streamlit.io)
   - Click on your app → Settings (⚙️ icon)

2. **Navigate to Secrets**
   - Click "Secrets" in the left sidebar

3. **Paste the secrets**
   - Copy the content from `streamlit_secrets.toml` (lines 9-34)
   - Paste directly into the secrets text area
   - **Do NOT include the comment lines or `[secrets]` header**
   - Only paste the three key-value pairs

4. **Save**
   - Click "Save"
   - Streamlit will automatically redeploy

## Verification

After saving, the app should:
- ✅ Read `UNABATED_API_KEY` from environment
- ✅ Read `KALSHI_API_KEY_ID` from environment  
- ✅ Read `KALSHI_PRIVATE_KEY_PEM` from environment

If you still get errors, check the app logs to see which specific secret is missing.
