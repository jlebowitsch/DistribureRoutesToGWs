## routeswitchbysubnet for Autoscaling groups of Gateways

The purpose of the script **routeswitchbysubnet** is to allow, in AWS VPCs, the automatic reassignments of route-targets to healthy gateways, when these gateways are monitored by an ELB.



### Prerequisites and installation

- Have gateways that act as routers (like Check Point vSEC gateways) up and running.  
- if the gateway has more than one ENI, make sure that the gateway knows to route to the subnets behind it through ethN where N is maximal. E.g., if the gateway has eth0 and eth1, make sure that each of the gateways knows that traffic to each of the protected subnets should be forwarded through eth1
- Set up an ELB to monitor the gateways. (the gateways can be part of an Autoscaling Group or not)
- Create an cloudwatch alarm that will send notification to some SNS topic if the number of unhealthy instances is larger than 0. Add to the alarm a notification, to the same topic, if the status changes back to OK
- Create a lambda function that uses the script routeswitchbysubnet.py, and have it triggered by notifications to that SNS topic 
- Change the value of the following variables that you see in the begining of the script:
    - *elbname*: the name of the loadbalancer that's monitoring your gateways (e.g., 'myELB')
    - *inputsubnets*: the list of subnets that needs to route through the gateways (e.g., ['subnet-1','subnet-2'])
    - *Routetargets*: the list of route prefixes traffic to which needs to flow through the gateways (e.g., ['0.0.0.0/0','10.1.0.0/16'])
- Change the "inputsubnets" variable
- By default the script uses a more advanced GW matching algorithm for each route. If you want to revert to an earlier version choose a value other than 1 to the param UseFancyDecision


### What the script does

Assuming UseFancyDecision=1 On execution, the script will 
1) Create a list of all the Gateways that are associated with the ELB
2) find all the route tables that are already associated with the subnets that have these routes, and create route tables for the subnets for which it doesn't find 
3) for each such route table, it finds what's the AZ that's the most common among all the subnets that use this route table
4) for each such route table, it's trying to see if there's a gateway that's better matching for it, given the AZs of the subnets using the route table, and the load on the GW

### Note to consider
currently Cloudwatch alerts must aggregate events at minimal time resultion of one minute. Thus this method of controlling routes will typically lag by one minute after a gateway first becomes "OutofService". More immdiate ways to trigger the the function RouteSwitchv2(elbname) would result in a more timely route modification

