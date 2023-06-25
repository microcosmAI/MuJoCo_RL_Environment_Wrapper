import json
import copy
import time
import numpy as np
from gymnasium.spaces import Box
from ray.rllib.env import MultiAgentEnv

try:
    from mujoco_parent import MuJoCoParent
    from helper import update_deep
except:
    from MuJoCo_Gym.mujoco_parent import MuJoCoParent
    from MuJoCo_Gym.helper import update_deep


class MuJoCoRL(MultiAgentEnv, MuJoCoParent):
    """ ToDo: description

    Attributes:
        config_dict (dict): Config dictionary containing user settings
    """
    def __init__(self, config_dict: dict):
        self.agents = config_dict.get("agents", [])
        self.xml_path = config_dict.get("xmlPath")
        self.info_json = config_dict.get("infoJson", "")
        self.render_mode = config_dict.get("renderMode", False)
        self.export_path = config_dict.get("exportPath")
        self.free_joint = config_dict.get("freeJoint", False)
        self.skip_frames = config_dict.get("skipFrames", 1)
        self.max_steps = config_dict.get("maxSteps", 1024)
        self.reward_functions = config_dict.get("rewardFunctions", [])
        self.done_functions = config_dict.get("doneFunctions", [])
        self.environment_dynamics = config_dict.get("environmentDynamics", [])
        self.agent_cameras = config_dict.get("agentCameras", False)

        self.timestep = 0
        self.start_time = time.time()

        self.action_routing = {"physical": [], "dynamic": {}}

        self.data_store = {agent: {} for agent in self.agents}

        MuJoCoParent.__init__(self, xml_paths=self.xml_path, export_path=self.export_path, render=self.render_mode, #ToDo: why is this None?
                              free_joint=self.free_joint, agent_cameras=self.agent_cameras)
        MultiAgentEnv.__init__(self)

        if self.info_json != "":
            json_file = open(self.info_json)
            self.info_json = json.load(json_file)
            self.info_name_list = [object["name"] for object in self.info_json["objects"]] #ToDo: is this not a df? looks like a list or sth
        else:
            self.info_json = None
            self.info_name_list = []

        self.environment_dynamics = [dynamic(self) for dynamic in self.environment_dynamics]
        self._observation_space = self.__create_observation_space()
        self.observation_space = self._observation_space[list(self._observation_space.keys())[0]]
        self.__check_dynamics(self.environment_dynamics)
        self.__check_reward_functions(self.reward_functions)
        self.__check_done_functions(self.done_functions)

        self._action_space = self.__create_action_space()
        self.action_space = self._action_space[list(self._action_space.keys())[0]]

    def __check_dynamics(self, environment_dynamics: list):
        """Check the output of the dynamic function in every Dynamic Class.
           I.e. whether the observation has the shape of and suits the domain of the observation space and whether
           the reward is a float.

        Parameters:
            environment_dynamics (list): List of all environment dynamic classes
        """
        for environment_dynamic_instance in environment_dynamics:
            actions = environment_dynamic_instance.action_space["low"]
            reward, observations = environment_dynamic_instance.dynamic(self.agents[0], actions)
            # check observations
            if not len(environment_dynamic_instance.observation_space["low"]) == len(observations):
                raise Exception(f"Observation, the second return variable of dynamic function, must match length"
                                f" of lower bound of observation space of {environment_dynamic_instance}")
            if not np.all(environment_dynamic_instance.observation_space["low"] <= observations):
                raise Exception(f"Observation, the second return variable of dynamic function, exceeds the lower bound"
                                f" on at least one axis of the observation space of {environment_dynamic_instance}")
            if not len(environment_dynamic_instance.observation_space["high"]) == len(observations):
                raise Exception(f"Observation, the second return variable of dynamic function, must match length of"
                                f" upper bound of observation space of {environment_dynamic_instance} must at least be"
                                f" three dimensional")
            if not np.all(environment_dynamic_instance.observation_space["high"] >= observations):
                raise Exception(f"Observation, the second return variable of dynamic function, exceeds the upper bound"
                                f" on at least one axis of the observation space of observation space"
                                f" of {environment_dynamic_instance}")
            # check reward
            if not (isinstance(reward, float) or isinstance(reward, int)):
                raise Exception(f"Reward, the first return variable of dynamic function"
                                f" of {environment_dynamic_instance}, must be a float")

    def __check_done_functions(self, done_functions: list):
        """Check the output of every done function.
           I.e. whether done is a boolean and whether reward is a float.

        Parameters:
            done_functions (list): List of all done functions
        """
        for doneFunction in done_functions:
            done = doneFunction(self, self.agents[0])
            # check done
            if not isinstance(done, int):
                raise Exception(f"Done, the first return variable of {doneFunction}, must be a boolean")
    
    def __check_reward_functions(self, reward_functions: list):
        """Check the output of every reward function.
           I.e. whether reward is a float.

        Parameters:
            reward_functions (list): List of all reward functions
        """
        for reward_function in reward_functions:
            reward = reward_function(self, self.agents[0])
            # check reward
            if not (isinstance(reward, float) or isinstance(reward, int)):
                raise Exception(f"Reward, the second return variable of {reward_function}, must be a float")


    def __create_action_space(self) -> dict:
        """Creates the action space for the current environment

        Returns:
            new_action_space (dict): A dictionary of action spaces for each agent
        """
        action_space = {}
        new_action_space = {}
        for agent in self.agents:
            # Gets the action space from the MuJoCo environment
            action_space[agent] = MuJoCoRL.get_action_space_mujoco(self, agent)
            self.action_routing["physical"] = [0, len(action_space[agent]["low"])]
            for dynamic in self.environment_dynamics:
                dyn_action_space = dynamic.action_space
                self.action_routing["dynamic"][dynamic.__class__.__name__] = [len(action_space[agent]["low"]),
                                                                              len(action_space[agent]["low"]) +
                                                                              len(dyn_action_space["low"])]
                action_space[agent]["low"] += dyn_action_space["low"]
                action_space[agent]["high"] += dyn_action_space["high"]

            new_action_space[agent] = Box(low=np.array(action_space[agent]["low"]),
                                          high=np.array(action_space[agent]["high"]))
        return new_action_space

    def __create_observation_space(self) -> dict:
        """Creates the observation space for the current environment

        Returns:
            new_observation_space (dict): A dictionary of observation spaces for each agent
        """
        observation_space = {}
        new_observation_space = {}
        for agent in self.agents:
            observation_space[agent] = MuJoCoRL.get_observation_space_mujoco(self, agent)
            # Get the action space for the environment dynamics
            for dynamic in self.environment_dynamics:
                observation_space[agent]["low"] += dynamic.observation_space["low"]
                observation_space[agent]["high"] += dynamic.observation_space["high"]
            new_observation_space[agent] = Box(low=np.array(observation_space[agent]["low"]), high=np.array(observation_space[agent]["high"]))
        return new_observation_space

    def step(self, action: dict) -> [dict, dict, dict, dict]:
        """Applies the actions for each agent and returns the observations, rewards, terminations, truncations,
           and infos for each agent

        Parameters:
            action (dict): A dictionary of actions for each agent

        Returns:
            observations (dict): A dictionary of observations for each agent
            rewards (dict): A dictionary of rewards for each agent
            terminations (dict): A dictionary of booleans indicating whether each agent is terminated
            truncations (dict): A dictionary of booleans indicating whether each agent is truncated
            infos (dict): A dictionary of dictionaries containing additional information for each agent
        """
        mujoco_actions = {key: action[key][self.action_routing["physical"][0]:self.action_routing["physical"][1]]
                          for key in action.keys()}
        self.apply_action(mujoco_actions, skip_frames=self.skip_frames)
        observations = {agent: self.get_sensor_data(agent) for agent in self.agents}
        rewards = {agent: 0 for agent in self.agents}

        data_store_copies = [copy.deepcopy(self.data_store) for _ in range(len(self.environment_dynamics))]
        original_data_store = copy.deepcopy(self.data_store)

        for i, dynamic in enumerate(self.environment_dynamics):
            self.data_store = data_store_copies[i]
            for agent in self.agents:
                dynamic_indizes = self.action_routing["dynamic"][dynamic.__class__.__name__]
                dynamic_actions = action[agent][dynamic_indizes[0]:dynamic_indizes[1]]
                reward, obs = dynamic.dynamic(agent, dynamic_actions)
                observations[agent] = np.concatenate((observations[agent], obs))
                rewards[agent] += reward

        self.data_store = original_data_store
        for data in data_store_copies:
            self.data_store = update_deep(self.data_store, data)

        for reward in self.reward_functions:
            rewards = {agent: rewards[agent] + reward(self, agent) for agent in self.agents}

        terminations = self.__check_terminations()

        truncations = {}
        if len(self.done_functions) == 0:
            truncations = {agent: terminations[agent] for agent in self.agents}
        else:
            for done in self.done_functions:
                truncations = {agent: terminations[agent] or done(self, agent) for agent in self.agents}
        truncations["__all__"] = any(truncations.values())

        self.timestep += 1

        infos = {agent: {} for agent in self.agents}
        return observations, rewards, terminations, truncations, infos

    def reset(self, *, seed: int = None, options=None) -> [dict, dict]:
        """Resets the environment and returns the observations for each agent

        Parameters:
            seed (int): Seed
            options (ToDo): options

        Returns:
            observations (dict): A dictionary of observations for each agent
            infos (dict): A dictionary of dictionaries containing additional information for each agent
        """
        MuJoCoParent.reset(self)
        observations = {agent: self.get_sensor_data(agent) for agent in self.agents}

        for dynamic in self.environment_dynamics:
            for agent in self.agents:
                actions = dynamic.action_space["low"]
                reward, obs = dynamic.dynamic(agent, actions)
                observations[agent] = np.concatenate((observations[agent], obs))
        self.data_store = {agent: {} for agent in self.agents}
        self.timestep = 0
        infos = {agent: {} for agent in self.agents}
        return observations, infos
    
    def filter_by_tag(self, tag: str) -> list:
        """Filter environment for object with specific tag

        Parameters:
            tag (str): Tag to be filtered for

        Returns:
            filtered (list): List of objects with the specified tag
        """
        filtered = []
        for object in self.info_json["objects"]:
            if "tags" in object.keys():
                if tag in object["tags"]:
                    data = self.get_data(object["name"])
                    filtered.append(data)
        return filtered

    def get_data(self, name: str) -> np.array:
        """Returns the data for an object/geom with the given name

        Parameters:
            name (str): The name of the object/geom

        Returns:
            data (np.array): The data for the object/geom
        """
        data = MuJoCoParent.get_data(self, name)
        if name in self.info_name_list:
            index = self.info_name_list.index(name)
            for key in self.info_json["objects"][index].keys():
                data[key] = self.info_json["objects"][index][key]
        return data

    def __get_observations(self) -> dict:
        """Returns the observations for each agent.
        # ToDo: to be implemented
        Returns:
            observations (dict): A dictionary of observations for each agent
        """
        observations = {}
        return observations

    def __check_terminations(self) -> dict:
        """Checks whether each agent is terminated

        Returns:
            terminations (dict): A dictionary of booleans indicating whether each agent is terminated #ToDo: i added this, is this correct?
        """
        if self.timestep >= self.max_steps:
            terminations = {agent: True for agent in self.agents}
        else:
            terminations = {agent: False for agent in self.agents}
        terminations["__all__"] = all(terminations.values())
        return terminations

    def __truncations_functions(self) -> dict:
        """Executes the list of truncation functions and returns the truncations for each agent
        ToDo: to be implemented
        Returns:
            truncations (dict): A dictionary of booleans indicating whether each agent is truncated
        """
        truncations = {}
        return truncations

    def __environment_functions(self) -> [dict, dict, dict]:
        """Executes the list of environment functions
        ToDo: to be implemented
        Returns:
            reward (dict): A dictionary of rewards for each agent
            observations (dict): A dictionary of observations for each agent
            infos (dict): A dictionary of dictionaries containing additional information for each agent
        """
        reward = {}
        observations = {}
        infos = {}
        return reward, observations, infos
