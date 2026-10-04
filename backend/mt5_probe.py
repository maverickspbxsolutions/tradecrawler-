import asyncio, os, json
from dotenv import load_dotenv
from metaapi_cloud_sdk import MetaApi
load_dotenv()

async def main():
    tok = os.environ["METAAPI_TOKEN"]
    acc_id = os.environ["MT5_ACCOUNT_ID"]
    api = MetaApi(tok)
    out = {}
    try:
        account = await api.metatrader_account_api.get_account(acc_id)
        await account.wait_connected()
        conn = account.get_rpc_connection()
        await conn.connect()
        await conn.wait_synchronized({"timeoutInSeconds": 120})
        info = await conn.get_account_information()
        out["account_information"] = info
    except Exception as e:
        out["error"] = str(e)
    with open("/tmp/mt5_probe.json", "w") as f:
        json.dump(out, f, default=str)
    print(json.dumps(out, default=str)[:600])

asyncio.run(main())
