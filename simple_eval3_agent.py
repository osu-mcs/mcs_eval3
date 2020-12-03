import machine_common_sense as mcs
import yaml
import os
import random
from exploration.controller_agent import ExploreAgent
from voe.voe_agent import VoeAgent
from voe.agency_voe_agent import AgencyVoeAgent


class Evaluation3_Agent:

    def __init__(self, seed=0):

        try:
            assert "unity_path.yaml" in os.listdir(os.getcwd())
            assert "mcs_config.yaml" in os.listdir(os.getcwd())
        except:
            raise FileExistsError("You might not set up mcs config and unity path yet. Please run 'bash setup_unity.sh'.")

        with open("./unity_path.yaml", 'r') as config_file:
            config = yaml.safe_load(config_file)

        self.controller = mcs.create_controller(
            os.path.join(config['unity_path'])
        )

        with open("./mcs_config.yaml", 'r') as config_file:
            config = yaml.safe_load(config_file)
        self.level = config['metadata']

        assert self.level in ['oracle', 'level1', 'level2']

        self.exploration_agent = ExploreAgent(self.controller, self.level)
        self.voe_agent = VoeAgent(self.controller, self.level)
        self.agency_voe_agent = AgencyVoeAgent(self.controller, self.level)

        #initilize VOE agent here also

        random.seed(seed)



    def run_scene(self, one_scene):
        scene_config, status = mcs.load_config_json_file(one_scene)
        goal_type = scene_config['goal']['category']
        if goal_type == "intuitive physics":
            self.voe_agent.run_scene(scene_config)
        elif goal_type == "agents":
            self.agency_voe_agent.run_scene(scene_config)
        elif goal_type == "retrieval":
            self.exploration_agent.run_scene(scene_config)
        else:
            print("Current goal type: {}".format(goal_type))
            raise ValueError("Goal type not clear! It should be either: , 'intuitive physics', 'agents' or 'retrieval'")


if __name__ == "__main__":

    agent = Evaluation3_Agent()

    goal_dir = "different_scenes"
    all_scenes = [os.path.join(goal_dir, one_scene) for one_scene in sorted(os.listdir(goal_dir))]

    for one_scene in all_scenes:
        agent.run_scene(one_scene)




