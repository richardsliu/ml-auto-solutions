# Copyright 2023 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Config file for a test job.

Note on dataclasses:

Why not use data classes? Before Python 3.10, init parameters for inherited
data classes are put in the order in which they are defined. Take this example:

```
class TestConfig(abc.ABC, Generic[A]):
  accelerator: A
  task_owner: Optional[str] = None

class TpuVmTest(TestConfig[Tpu]):
  test_name: str
```

`TpuVmTest.__init__`'s signature will be something like this:

```
def __init__(self, accelerator, task_owner=None, test_name):
  ...
```

Putting required positional arguments after required ones is, of course, not
allowed. This prevents us from defining any optional parameters on the parent
class. Python 3.10 adds keyword-only dataclass fields, but we have to get that
functionality from `attrs` for now.

When Composer updates to a recent Python version, we can use dataclasses.
"""

import abc
import json
import os
import shlex
from typing import Any, Generic, Iterable, List, Optional, TypeVar

import attrs
import datetime
from dags.vm_resource import TpuVersion, CpuVersion


class Accelerator(abc.ABC):
  """Represents an ML accelerator."""

  @property
  @abc.abstractmethod
  def name(self) -> str:
    """Name of this ML accelerator."""
    raise NotImplementedError


@attrs.define
class Tpu(Accelerator):
  """Represents a single Cloud TPU instance.

  Attributes:
    version: TPU device version.
    cores: Physical cores in this TPU type, i.e. the number of cores in the
      name.
    runtime_version: Runtime image version.
    network: The network that a TPU will be a part of.
    subnetwork: The subnetwork that a TPU will be a part of.
    reserved: The flag to define if a TPU is a Cloud reservation.
  """

  version: TpuVersion
  cores: int
  runtime_version: Optional[str] = None
  network: str = 'default'
  subnetwork: str = 'default'
  reserved: bool = False
  preemptible: bool = False

  @property
  def name(self):
    """Name of this TPU type in the Cloud TPU API (e.g. 'v4-8')."""
    return f'v{self.version.value}-{self.cores}'


@attrs.define
class Gpu(Accelerator):
  """Represents a single Cloud GPU instance.

  Attributes:
    machine_type: The host type of the GPU. E.g., `a2-highgpu-1g`.
    image_family: Family of the image.
    count: Number of the GPU devices.
    accelerator_type: Type of the accelerator. E.g., `nvidia-test-v100`.
    runtime_version: Runtime image version.
    network: The network that a GPU will be a part of.
    subnetwork: The subnetwork that a GPU will be a part of.
  """

  machine_type: str
  image_family: str
  count: int
  accelerator_type: str
  runtime_version: Optional[str] = None
  network: Optional[str] = None
  subnetwork: Optional[str] = None

  @property
  def name(self):
    """Name of this GPU type in the Cloud GPU API (e.g. 'a2-highgpu-1g')."""
    return self.accelerator_type


@attrs.define
class Cpu(Accelerator):
  """Represents a single Cloud CPU instance.

  Attributes:
    device_type: CPU device type. E.g., `m1-megamem-96-1` or `n2-standard-64-1`.
  """

  device_type: CpuVersion
  machine_count: int

  @property
  def name(self):
    """Name of this CPU type (e.g. 'n2-standard-64-1')."""
    return f'{self.device_type.value}-{self.machine_count}'


A = TypeVar('A', bound=Accelerator)


@attrs.define
class TestConfig(abc.ABC, Generic[A]):
  """Base class for end-to-end test configurations.

  Attributes:
    accelerator: Accelerator type required for this test.
    timeout: Test timeout.
    task_owner: Task owner username or link.
    gcs_subfolder: Subfolder name for default GCS bucket.
  """

  accelerator: A
  timeout: Optional[datetime.timedelta] = attrs.field(
      default=None, kw_only=True
  )
  task_owner: str = attrs.field(default='unowned', kw_only=True)
  gcs_subfolder: str = attrs.field(default='unowned', kw_only=True)

  @property
  @abc.abstractmethod
  def benchmark_id(self) -> str:
    """Unique key for metrics generated by this test."""
    raise NotImplementedError()

  @property
  def setup_script(self) -> Optional[str]:
    """Optional script to run once when the accelerator is created."""
    return None

  @property
  @abc.abstractmethod
  def test_script(self) -> str:
    """Script to run on accelerator machine.

    The exit code of this script will be the test result.
    """
    raise NotImplementedError()


@attrs.define
class TpuVmTest(TestConfig[Tpu]):
  """Test config that runs on a single Cloud TPU VM instance.

  Attributes:
    test_name: Unique name for this test/model.
    set_up_cmds: List of commands to run once when TPU is created.
    run_model_cmds: List of commands to run the model under test.
    num_slices: Number of TPU slices.
  """

  test_name: str
  set_up_cmds: Iterable[str]
  run_model_cmds: Iterable[str]
  num_slices: int = attrs.field(default=1, kw_only=True)

  @property
  def benchmark_id(self) -> str:
    return (
        f'{self.test_name}-{self.accelerator.name}'
        if self.num_slices == 1
        else f'{self.test_name}-{self.num_slices}x{self.accelerator.name}'
    )

  @property
  def setup_script(self) -> Optional[str]:
    return '\n'.join(('set -xue', *self.set_up_cmds))

  @property
  def test_script(self) -> str:
    return '\n'.join(('set -xue', *self.run_model_cmds))


@attrs.define
class GpuVmTest(TestConfig[Gpu]):
  """Test config that runs on a single Cloud GPU VM instance.

  Attributes:
    test_name: Unique name for this test/model.
    set_up_cmds: List of commands to run once when GPU is created.
    run_model_cmds: List of commands to run the model under test.
  """

  test_name: str
  set_up_cmds: Iterable[str]
  run_model_cmds: Iterable[str]

  @property
  def benchmark_id(self) -> str:
    return f'{self.test_name}-{self.accelerator.name}'

  @property
  def setup_script(self) -> Optional[str]:
    return '\n'.join(('set -xue', *self.set_up_cmds))

  @property
  def test_script(self) -> str:
    return '\n'.join(('set -xue', *self.run_model_cmds))


@attrs.define
class CpuGkeTest(TestConfig[Cpu]):
  """Test config that runs on a single Cloud CPU instance in GKE cluster.

  Attributes:
    test_name: Unique name for this test/model.
    cluster_name: Name of the cluster that has provisioned CPUs.
    docker_image: Image of the docker to run.
    set_up_cmds: List of commands to run once when CPU is created.
    run_model_cmds: List of commands to run the model under test.
    startup_time_out_in_sec: Timeout to start up the pod.
    num_slices: Number of CPU slices.
  """

  test_name: str
  cluster_name: str
  docker_image: str
  set_up_cmds: Iterable[str]
  run_model_cmds: Iterable[str]
  startup_time_out_in_sec: int = attrs.field(default=300, kw_only=True)
  num_slices: int = attrs.field(default=1, kw_only=True)

  @property
  def benchmark_id(self) -> str:
    return f'{self.test_name}-{self.accelerator.name}'

  @property
  def setup_script(self) -> Optional[str]:
    return ';'.join(('set -xue', *self.set_up_cmds))

  @property
  def test_script(self) -> str:
    return ';'.join(('set -xue', *self.run_model_cmds))


@attrs.define
class TpuGkeTest(TestConfig[Tpu]):
  """Test config that runs on a single Cloud TPU instance in GKE cluster.

  Attributes:
    test_name: Unique name for this test/model.
    cluster_name: Name of the cluster that has provisioned TPUs.
    docker_image: Image of the docker to run.
    set_up_cmds: List of commands to run once when TPU is created.
    run_model_cmds: List of commands to run the model under test.
    startup_time_out_in_sec: Timeout to start up the pod.
    num_slices: Number of TPU slices.
  """

  test_name: str
  cluster_name: str
  docker_image: str
  set_up_cmds: Iterable[str]
  run_model_cmds: Iterable[str]
  startup_time_out_in_sec: int = attrs.field(default=300, kw_only=True)
  num_slices: int = attrs.field(default=1, kw_only=True)

  @property
  def benchmark_id(self) -> str:
    return (
        f'{self.test_name}-{self.accelerator.name}'
        if self.num_slices == 1
        else f'{self.test_name}-{self.num_slices}x{self.accelerator.name}'
    )

  @property
  def setup_script(self) -> Optional[str]:
    return ';'.join(('set -xue', *self.set_up_cmds))

  @property
  def test_script(self) -> str:
    return ';'.join(('set -xue', *self.run_model_cmds))


def _load_compiled_jsonnet(test_name: str) -> Any:
  # TODO(wcromar): Parse GPU tests too
  config_dir = os.environ.get(
      'XLMLTEST_CONFIGS', '/home/airflow/gcs/dags/dags/jsonnet'
  )
  test_path = os.path.join(config_dir, test_name)
  with open(test_path, 'r') as f:
    test = json.load(f)

  return test


@attrs.define
class GpuXpkTest(TestConfig[Gpu]):
  """Test config that runs on a single Cloud GPU instance in GKE cluster.

  Attributes:
    test_name: Unique name for this test/model.
    cluster_name: Name of the cluster that has provisioned GPUs.
    docker_image: Image of the docker to run.
    set_up_cmds: List of commands to run once when GPU is created.
    run_model_cmds: List of commands to run the model under test.
    startup_time_out_in_sec: Timeout to start up the pod.
    num_slices: Number of GPU slices.
  """

  test_name: str
  cluster_name: str
  docker_image: str
  set_up_cmds: Iterable[str]
  run_model_cmds: Iterable[str]
  startup_time_out_in_sec: int = attrs.field(default=300, kw_only=True)
  num_slices: int = attrs.field(default=1, kw_only=True)

  @property
  def benchmark_id(self) -> str:
    return f'{self.test_name}-{self.accelerator.name}'

  @property
  def setup_script(self) -> Optional[str]:
    return ';'.join(self.set_up_cmds)

  @property
  def test_script(self) -> str:
    return ';'.join(self.run_model_cmds)


@attrs.define
class JSonnetTpuVmTest(TestConfig[Tpu]):
  """Convert legacy JSonnet test configs into a TestConfig.

  Do not construct directly. Instead, use the `from_*` factory functions which
  parse pre-compiled JSonnet test configs.

  Attributes:
    test_name: Unique name of this test/model.
    setup: Multi-line script that configures the TPU instance.
    exports: Extra setup commands to run in same shell as test_command.
    test_command: Command and arguments to execute on the TPU VM.
    num_slices: Number of TPU slices.
  """

  test_name: str
  setup: str
  exports: str
  test_command: List[str]
  num_slices: int = 1

  @staticmethod
  def _from_json_helper(
      test: Any,
      setup: str,
      exports: str,
      test_command: List[str],
      reserved: bool,
      network: str,
      subnetwork: str,
  ):
    return JSonnetTpuVmTest(
        test_name=test['testName'],
        accelerator=Tpu(
            version=TpuVersion(
                str(test['accelerator']['version'])
                + test['accelerator']['variant']
            ),
            cores=test['accelerator']['size'],
            runtime_version=test['tpuSettings']['softwareVersion'],
            reserved=reserved,
            network=network,
            subnetwork=subnetwork,
        ),
        setup=setup,
        exports=exports,
        test_command=test_command,
        timeout=datetime.timedelta(seconds=test['timeout']),
    )

  @staticmethod
  def from_jax(
      test_name: str,
      reserved: bool = False,
      network='default',
      subnetwork='default',
  ):
    """Parses a compiled legacy JSonnet config test from `tests/jax`."""
    test = _load_compiled_jsonnet(test_name)
    return JSonnetTpuVmTest._from_json_helper(
        test,
        # TODO(wcromar): make this less hacky
        setup=test['setup'],
        exports='',
        test_command=['bash', '-c', test['runTest']],
        reserved=reserved,
        network=network,
        subnetwork=subnetwork,
    )

  @staticmethod
  def from_pytorch(
      test_name: str,
      reserved: bool = False,
      network='default',
      subnetwork='default',
  ):
    """Parses a compiled legacy JSonnet test config from `tests/pytorch`."""
    test = _load_compiled_jsonnet(test_name)
    return JSonnetTpuVmTest._from_json_helper(
        test,
        setup=test['tpuSettings']['tpuVmPytorchSetup']
        # HACK: Extra setup assumes a new shell in home directory
        + '\ncd ~\n' + test['tpuSettings']['tpuVmExtraSetup'],
        exports=test['tpuSettings']['tpuVmExports'],
        test_command=test['command'],
        reserved=reserved,
        network=network,
        subnetwork=subnetwork,
    )

  @property
  def benchmark_id(self) -> str:
    return self.test_name

  @property
  def setup_script(self) -> Optional[str]:
    return '\n'.join(['set -xue', self.setup])

  # TODO(wcromar): replace configmaps
  @property
  def test_script(self) -> str:
    return '\n'.join([
        'set -xue',
        self.exports,
        ' '.join(shlex.quote(s) for s in self.test_command),
    ])


@attrs.define
class GpuGkeTest(TestConfig[Gpu]):
  """
  Attributes:
    test_name: Unique name of this test/model.
    test_command: Command and arguments to execute on the TPU VM.
    entrypoint: Multi-line script that configures the GPU instance and invokes
      `test_command`.
    docker_image: Image for main test container.
    num_hosts: Number of GPU hosts.
  """

  test_name: str
  entrypoint_script: List[str]
  test_command: List[str]
  docker_image: str
  num_hosts: int = 1
  gcs_subfolder: str = '/tmp/'

  @staticmethod
  def from_pytorch(test_name: str):
    """Parses a compiled legacy JSonnet test config from `tests/pytorch`."""
    test = _load_compiled_jsonnet(test_name)

    return GpuGkeTest(
        test_name=test_name,
        docker_image=f'{test["image"]}:{test["imageTag"]}',
        accelerator=Gpu(
            machine_type='n/a',
            image_family='n/a',
            runtime_version='n/a',
            count=test['accelerator']['count'],
            accelerator_type=test['accelerator']['accelerator_type'],
        ),
        entrypoint_script=test['entrypoint'],
        test_command=test['command'],
        num_hosts=test['accelerator']['num_hosts'],
        timeout=datetime.timedelta(seconds=test['timeout']),
    )

  @property
  def benchmark_id(self) -> str:
    return f'{self.test_name}-{self.accelerator.name}'

  @property
  def setup_script(self) -> str:
    return shlex.join(self.entrypoint_script)

  @property
  def test_script(self) -> str:
    return shlex.join(self.test_command)
