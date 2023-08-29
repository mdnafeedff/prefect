import datetime
from time import sleep

import anyio
import pytest

from prefect import flow, serve
from prefect.client.orchestration import PrefectClient
from prefect.client.schemas.objects import StateType
from prefect.runner import Runner


@flow
def dummy_flow_1():
    pass


@flow
def dummy_flow_2():
    pass


@flow()
def tired_flow():
    print("I am so tired...")

    for _ in range(100):
        print("zzzzz...")
        sleep(5)


class TestServe:
    async def test_serve_can_create_multiple_deployments(
        self, prefect_client: PrefectClient
    ):
        async with anyio.create_task_group() as tg:
            with anyio.CancelScope(shield=True):
                deployment_1 = dummy_flow_1.to_deployment(__file__, interval=3600)
                deployment_2 = dummy_flow_2.to_deployment(__file__, cron="* * * * *")

                tg.start_soon(serve, deployment_1, deployment_2)

                await anyio.sleep(1)
                tg.cancel_scope.cancel()

        deployment = await prefect_client.read_deployment_by_name(
            name="dummy-flow-1/test_runner"
        )

        assert deployment is not None
        assert deployment.schedule.interval == datetime.timedelta(seconds=3600)

        deployment = await prefect_client.read_deployment_by_name(
            name="dummy-flow-2/test_runner"
        )

        assert deployment is not None
        assert deployment.schedule.cron == "* * * * *"

    async def test_serve_can_execute_scheduled_flow_runs(
        self, prefect_client: PrefectClient
    ):
        async with anyio.create_task_group() as tg:
            deployment = dummy_flow_1.to_deployment("test")

            tg.start_soon(serve, deployment)

            await anyio.sleep(1)

            deployment = await prefect_client.read_deployment_by_name(
                name="dummy-flow-1/test"
            )

            flow_run = await prefect_client.create_flow_run_from_deployment(
                deployment_id=deployment.id
            )
            # Need to wait for polling loop to pick up flow run and then
            # finish execution
            for _ in range(15):
                await anyio.sleep(1)
                flow_run = await prefect_client.read_flow_run(flow_run_id=flow_run.id)
                if flow_run.state.is_completed():
                    break

            tg.cancel_scope.cancel()

        assert flow_run.state.is_completed()

    async def test_serve_can_cancel_flow_runs(self, prefect_client: PrefectClient):
        async with anyio.create_task_group() as tg:
            deployment = tired_flow.to_deployment("test")

            tg.start_soon(serve, deployment)

            await anyio.sleep(1)

            deployment = await prefect_client.read_deployment_by_name(
                name="tired-flow/test"
            )

            flow_run = await prefect_client.create_flow_run_from_deployment(
                deployment_id=deployment.id
            )
            # Need to wait for polling loop to pick up flow run and
            # start execution
            for _ in range(15):
                await anyio.sleep(1)
                flow_run = await prefect_client.read_flow_run(flow_run_id=flow_run.id)
                if flow_run.state.is_running():
                    break

            await prefect_client.set_flow_run_state(
                flow_run_id=flow_run.id,
                state=flow_run.state.copy(
                    update={"name": "Cancelled", "type": StateType.CANCELLED}
                ),
            )

            # Need to wait for polling loop to pick up flow run and then
            # finish cancellation
            for _ in range(15):
                await anyio.sleep(1)
                flow_run = await prefect_client.read_flow_run(flow_run_id=flow_run.id)
                if flow_run.state.is_cancelled():
                    break

            tg.cancel_scope.cancel()

        assert flow_run.state.is_cancelled()


class TestRunner:
    async def test_add_flows_to_runner(self, prefect_client: PrefectClient):
        """Runner.add should create a deployment for the flow passed to it"""
        runner = Runner()

        deployment_id_1 = await runner.add(dummy_flow_1, __file__, interval=3600)
        deployment_id_2 = await runner.add(dummy_flow_2, __file__, cron="* * * * *")

        deployment_1 = await prefect_client.read_deployment(deployment_id_1)
        deployment_2 = await prefect_client.read_deployment(deployment_id_2)

        assert deployment_1 is not None
        assert deployment_1.name == "test_runner"
        assert deployment_1.schedule.interval == datetime.timedelta(seconds=3600)

        assert deployment_2 is not None
        assert deployment_2.name == "test_runner"
        assert deployment_2.schedule.cron == "* * * * *"

    async def test_add_fails_with_multiple_schedules(self):
        runner = Runner()

        with pytest.raises(
            ValueError, match="Only one of interval, cron, or rrule can be provided."
        ):
            await runner.add(dummy_flow_1, name="test", interval=3600, cron="* * * * *")

        with pytest.raises(
            ValueError, match="Only one of interval, cron, or rrule can be provided."
        ):
            await runner.add(
                dummy_flow_1, name="test", interval=3600, rrule="FREQ=MINUTELY"
            )

        with pytest.raises(
            ValueError, match="Only one of interval, cron, or rrule can be provided."
        ):
            await runner.add(
                dummy_flow_1, name="test", cron="* * * * *", rrule="FREQ=MINUTELY"
            )

    async def test_add_deployments_to_runner(self, prefect_client: PrefectClient):
        """Runner.add_deployment should apply the deployment passed to it"""
        runner = Runner()

        deployment_1 = dummy_flow_1.to_deployment(__file__, interval=3600)
        deployment_2 = dummy_flow_2.to_deployment(__file__, cron="* * * * *")

        deployment_id_1 = await runner.add_deployment(deployment_1)
        deployment_id_2 = await runner.add_deployment(deployment_2)

        deployment_1 = await prefect_client.read_deployment(deployment_id_1)
        deployment_2 = await prefect_client.read_deployment(deployment_id_2)

        assert deployment_1 is not None
        assert deployment_1.name == "test_runner"
        assert deployment_1.schedule.interval == datetime.timedelta(seconds=3600)

        assert deployment_2 is not None
        assert deployment_2.name == "test_runner"
        assert deployment_2.schedule.cron == "* * * * *"

    async def test_runner_can_pause_schedules_on_stop(
        self, prefect_client: PrefectClient
    ):
        runner = Runner()

        deployment_1 = dummy_flow_1.to_deployment(__file__, interval=3600)
        deployment_2 = dummy_flow_2.to_deployment(__file__, cron="* * * * *")

        await runner.add_deployment(deployment_1)
        await runner.add_deployment(deployment_2)

        async with anyio.create_task_group() as tg:
            tg.start_soon(runner.start)

            deployment_1 = await prefect_client.read_deployment_by_name(
                name="dummy-flow-1/test_runner"
            )
            deployment_2 = await prefect_client.read_deployment_by_name(
                name="dummy-flow-2/test_runner"
            )

            assert deployment_1.is_schedule_active

            assert deployment_2.is_schedule_active

            runner.stop()

        deployment_1 = await prefect_client.read_deployment_by_name(
            name="dummy-flow-1/test_runner"
        )
        deployment_2 = await prefect_client.read_deployment_by_name(
            name="dummy-flow-2/test_runner"
        )

        assert not deployment_1.is_schedule_active

        assert not deployment_2.is_schedule_active

    async def test_runner_executes_flow_runs(self, prefect_client: PrefectClient):
        runner = Runner(query_seconds=2)

        deployment = dummy_flow_1.to_deployment(__file__)

        await runner.add_deployment(deployment)

        async with anyio.create_task_group() as tg:
            tg.start_soon(runner.start)

            deployment = await prefect_client.read_deployment_by_name(
                name="dummy-flow-1/test_runner"
            )

            flow_run = await prefect_client.create_flow_run_from_deployment(
                deployment_id=deployment.id
            )

            # Need to wait for polling loop to pick up flow run and then
            # finish execution
            for _ in range(15):
                await anyio.sleep(1)
                flow_run = await prefect_client.read_flow_run(flow_run_id=flow_run.id)
                if flow_run.state.is_completed():
                    break

            runner.stop()

        assert flow_run.state.is_completed()

    async def test_runner_can_cancel_flow_runs(self, prefect_client: PrefectClient):
        runner = Runner(query_seconds=2)

        deployment = tired_flow.to_deployment(__file__)

        await runner.add_deployment(deployment)

        async with anyio.create_task_group() as tg:
            tg.start_soon(runner.start)

            deployment = await prefect_client.read_deployment_by_name(
                name="tired-flow/test_runner"
            )

            flow_run = await prefect_client.create_flow_run_from_deployment(
                deployment_id=deployment.id
            )

            # Need to wait for polling loop to pick up flow run and
            # start execution
            for _ in range(15):
                await anyio.sleep(1)
                flow_run = await prefect_client.read_flow_run(flow_run_id=flow_run.id)
                if flow_run.state.is_running():
                    break

            await prefect_client.set_flow_run_state(
                flow_run_id=flow_run.id,
                state=flow_run.state.copy(
                    update={"name": "Cancelled", "type": StateType.CANCELLED}
                ),
            )

            # Need to wait for polling loop to pick up flow run and then
            # finish cancellation
            for _ in range(15):
                await anyio.sleep(1)
                flow_run = await prefect_client.read_flow_run(flow_run_id=flow_run.id)
                if flow_run.state.is_cancelled():
                    break

            runner.stop()
            tg.cancel_scope.cancel()

        assert flow_run.state.is_cancelled()

    async def test_runner_can_execute_a_single_flow_run(
        self, prefect_client: PrefectClient
    ):
        runner = Runner()

        deployment_id = await dummy_flow_1.to_deployment(__file__).apply()

        flow_run = await prefect_client.create_flow_run_from_deployment(
            deployment_id=deployment_id
        )
        await runner.execute_flow_run(flow_run.id)

        flow_run = await prefect_client.read_flow_run(flow_run_id=flow_run.id)
        assert flow_run.state.is_completed()
